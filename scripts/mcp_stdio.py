#!/usr/bin/env python3
"""stdio transport bridge to the authenticated SDK-backed HTTP MCP server.
Only JSON-RPC is written to stdout. Credentials are loaded from a private config,
never from command arguments or printed. No additional packages are required.
"""
import json,os,sys,urllib.request,urllib.error
from pathlib import Path


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, destination):
        return None


opener = urllib.request.build_opener(NoRedirects())
config=Path(os.environ.get('NIRA_MCP_CONFIG',Path(__file__).resolve().parents[1]/'.private/mcp.json'))
try:
 data=json.loads(config.read_text());url=data['url'];token=data['token']
 if not url.startswith('https://') and not url.startswith(('http://localhost:','http://127.0.0.1:')):raise ValueError()
except Exception:
 print('Nira MCP: configure NIRA_MCP_CONFIG with a private url/token JSON file.',file=sys.stderr);sys.exit(1)
version=None
for line in sys.stdin:
 if not line.strip():continue
 request_id=None;has_id=False
 try:
  if len(line.encode())>1024*1024:raise ValueError('Request too large')
  payload=json.loads(line);has_id='id' in payload;request_id=payload.get('id')
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
  if version:headers['MCP-Protocol-Version']=version
  request=urllib.request.Request(url,data=line.encode(),headers=headers,method='POST')
  with opener.open(request,timeout=75) as response:
   raw=response.read(2*1024*1024)
   if not raw:continue
   result=json.loads(raw)
   if payload.get('method')=='initialize':version=result.get('result',{}).get('protocolVersion')
   print(json.dumps(result,separators=(',',':')),flush=True)
 except urllib.error.HTTPError as error:
  if has_id:print(json.dumps({'jsonrpc':'2.0','id':request_id,'error':{'code':-32001,'message':'Nira HTTP request rejected ('+str(error.code)+'). Check endpoint, token scope and token validity.'}}),flush=True)
 except Exception:
  if has_id:print(json.dumps({'jsonrpc':'2.0','id':request_id,'error':{'code':-32603,'message':'Nira bridge failed. Check connectivity and private configuration.'}}),flush=True)
