mkdir -p venv localdata
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install numpy scikit-learn
cd localdata && wget -q https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.train
cd localdata && wget -q https://archive.ics.uci.edu/ml/machine-learning-databases/monks-problems/monks-2.test
cd localdata && wget -q https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.data
cd localdata && wget -q https://archive.ics.uci.edu/ml/machine-learning-databases/breast-cancer-wisconsin/breast-cancer-wisconsin.names
cd localdata && python3 -c "import numpy as np; d=np.loadtxt('monks-2.train'); np.savetxt('monks-2-train.csv', d, delimiter=','); d=np.loadtxt('monks-2.test'); np.savetxt('monks-2-test.csv', d, delimiter=',')"
cd localdata && python3 -c "import numpy as np; d=np.genfromtxt('breast-cancer-wisconsin.data', delimiter=','); np.savetxt('breast-cancer-wisconsin.csv', d, delimiter=',')"
