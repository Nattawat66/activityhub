import json
from channels.generic.websocket import AsyncWebsocketConsumer


class NotificationsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return

        # Custom User uses email as PK. Group names can't contain @ or .
        safe_email = str(user.pk).replace('@', '_').replace('.', '_')
        self.user_group_name = f"notif_{safe_email}"

        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
        except Exception:
            pass

    async def notify(self, event):
        # event should contain 'payload' key
        payload = event.get('payload') or {}
        await self.send(text_data=json.dumps({"type": "notification", "payload": payload}))
