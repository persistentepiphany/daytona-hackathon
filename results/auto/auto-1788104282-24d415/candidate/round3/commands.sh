mkdir -p localdata venv
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install numpy scipy scikit-learn pandas requests
wget -q -O localdata/monks-2.train https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.train
wget -q -O localdata/monks-2.test https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.test
wget -q -O localdata/breast-cancer-wisconsin.data https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data
wget -q -O localdata/breast-cancer-wisconsin.names https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.names
wget -q -O localdata/ILPD.csv https://archive.ics.uci.edu/ml/machine-learning-databases/00225/Indian%20Liver%20Patient%20Dataset%20(ILPD).csv
wget -q -O localdata/ozone.csv https://archive.ics.uci.edu/static/public/172/ozone+level+dataset.zip
unzip -o -d localdata localdata/ozone.csv
wget -q -O localdata/statlog.zip https://archive.ics.uci.edu/ml/machine-learning-databases/statlog/australian/australian.dat
wget -q -O localdata/energy.csv https://archive.ics.uci.edu/ml/machine-learning-databases/00242/ENB2012_data.xlsx
