python3 -m venv venv
venv/bin/pip install --quiet numpy scikit-learn requests
mkdir -p localdata
wget -q -O localdata/monks-2.train https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.train
wget -q -O localdata/monks-2.test https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.test
wget -q -O localdata/bcw.data https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data
wget -q -O localdata/bcw.names https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.names
