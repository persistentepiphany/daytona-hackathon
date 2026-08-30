set -e
python3 -m venv ./venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install numpy scikit-learn requests
mkdir -p localdata
curl -s -o localdata/monks-2.train https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.train
curl -s -o localdata/monks-2.test https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.test
curl -s -o localdata/breast-cancer-wisconsin.data https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data
curl -s -o localdata/breast-cancer-wisconsin.names https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.names
chmod +x smoke.sh
