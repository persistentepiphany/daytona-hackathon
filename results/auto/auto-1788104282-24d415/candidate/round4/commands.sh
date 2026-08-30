mkdir -p localdata venv
python3 -m venv venv
venv/bin/pip install --quiet numpy scikit-learn
wget -q -O localdata/monks-2.train https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.train
wget -q -O localdata/monks-2.test https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.test
wget -q -O localdata/bcw.data https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data
wget -q -O localdata/bcw.names https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.names
wget -q -O localdata/ozone.zip https://archive.ics.uci.edu/static/public/172/ozone-level-detection.zip && unzip -q -o localdata/ozone.zip -d localdata/ && rm localdata/ozone.zip
venv/bin/pip install --quiet requests
venv/bin/python -c "from dataio import *; X, y = load_split('localdata', 'train'); print('smoke load ok', X.shape, y.shape)"
venv/bin/python train.py --claim c_brf_monks --seed 1 --set data.dir=localdata
