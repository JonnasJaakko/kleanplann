import ast
import sys

files = [
    'app.py', 'scheduler.py', 'sanitarnorm.py', 'cost_calculator.py',
    'screens/__init__.py', 'screens/start_screen.py',
    'screens/plan_screen.py', 'screens/zone_screen.py',
    'screens/report_screen.py', 'screens/norms_screen.py'
]

ok = True
for f in files:
    try:
        with open(f, encoding='utf-8') as fh:
            ast.parse(fh.read())
        print(f"OK: {f}")
    except SyntaxError as e:
        ok = False
        print(f"SYNTAX ERROR in {f}: {e}")

if ok:
    print("ALL SYNTAX OK")
else:
    sys.exit(1)