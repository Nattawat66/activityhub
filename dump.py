import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'activityhub.settings')
django.setup()

from django.core.management import call_command

with open('datadump2.json', 'w', encoding='utf-8') as f:
    call_command(
        'dumpdata',
        '--natural-foreign',
        '--exclude', 'auth.permission',
        '--exclude', 'contenttypes',
        '--indent', '2',
        stdout=f
    )

print("Done! datadump2.json created.")