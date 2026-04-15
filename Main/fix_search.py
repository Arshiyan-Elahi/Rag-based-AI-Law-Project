# coding=utf-8
import sys

with open('backend/app/routes.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if '# Sort logic mimicking the frontend' in line:
        new_lines.extend([
            '    # Decisions\n',
            '    decisions = db.query(Decision).filter(\n',
            '        Decision.tenant_id == FIXED_TENANT_ID,\n',
            '        or_(\n',
            '            Decision.title.ilike(query),\n',
            '            Decision.decision_number.ilike(query),\n',
            '            Decision.decision_type.ilike(query),\n',
            '            Decision.decision_statement.ilike(query),\n',
            '            Decision.rationale_text.ilike(query)\n',
            '        )\n',
            '    ).all()\n',
            '    \n',
            '    for dec in decisions:\n',
            '        desc = dec.decision_statement or dec.rationale_text or "Keine Beschreibung"\n',
            '        results.append({\n',
            '            "id": str(dec.id),\n',
            '            "type": "decision",\n',
            '            "typeLabel": "Decision",\n',
            '            "metadata": f"{dec.decision_number or \'\'} · {dec.decision_type or \'Allgemein\'}",\n',
            '            "matchPercent": 80,\n',
            '            "title": dec.title or dec.decision_number or "Unbekannte Entscheidung",\n',
            '            "excerpt": (desc[:140] + \'...\') if len(desc) > 140 else desc,\n',
            '            "badges": [],\n',
            '            "sourceIcon": "❓",\n',
            '            "sourceColorClass": "source-decision"\n',
            '        })\n\n'
        ])
    new_lines.append(line)

with open('backend/app/routes.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Done!')
