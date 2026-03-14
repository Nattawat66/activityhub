import json

with open('datadump_fixed.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

models = {}
for obj in data:
    m = obj['model']
    models[m] = models.get(m, 0) + 1

for m, count in sorted(models.items()):
    print(f"{m}: {count}")