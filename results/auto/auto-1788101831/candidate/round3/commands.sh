python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install numpy scikit-learn requests pandas
mkdir -p localdata
venv/bin/python -c "import requests, os; d='localdata'; os.makedirs(d, exist_ok=True); urls=[('breast-cancer-wisconsin.data','https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data'), ('monks-2.train','https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.train'), ('ILPD.csv','https://archive.ics.uci.edu/static/public/225/ilpd.csv')]; [open(f'{d}/{n}','wb').write(requests.get(u, timeout=30).content) for n,u in urls]"
