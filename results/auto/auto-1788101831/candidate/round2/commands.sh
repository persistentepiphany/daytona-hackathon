mkdir -p localdata venv
python3 -m venv venv
venv/bin/pip install --quiet numpy scikit-learn pandas requests
venv/bin/python -c "import requests, os, json, sys; d='localdata'; os.makedirs(d, exist_ok=True); [open(f'{d}/{n}', 'wb').write(requests.get(u).content) for n,u in [('monks-2.train', 'https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.train'), ('monks-2.test', 'https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.test'), ('breast-cancer-wisconsin.data', 'https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data'), ('ILPD.csv', 'https://archive.ics.uci.edu/static/public/225/ilpd.csv')] ]"
chmod +x smoke.sh
