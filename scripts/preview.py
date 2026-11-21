import os,sys,pathlib,json
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from backend.app import create_app
import uvicorn
config=json.loads(pathlib.Path(os.environ['NIRA_CONFIG']).read_text())
uvicorn.run(create_app(config),host='127.0.0.1',port=8865,log_level='warning')
