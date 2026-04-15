"""
Index Page Handler
==================
Serves the homepage with session info and API documentation.
"""

from aiohttp import web


async def handle_index(request: web.Request) -> web.Response:
    """首页说明"""
    host = request.headers.get("Host", "localhost:8080")
    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>VSCode Tunnel Gateway</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 60px auto; padding: 0 20px; color: #333; }}
h1 {{ border-bottom: 2px solid #007acc; padding-bottom: 10px; color: #007acc; }}
code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 4px; font-size: 14px; }}
pre {{ background: #1e1e1e; color: #d4d4d4; padding: 16px; border-radius: 8px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #007acc; color: white; }}
.endpoint {{ font-family: monospace; }}
</style>
</head>
<body>
<h1>VSCode Tunnel Gateway</h1>
<p>通过 WebSocket 隧道安全地访问容器内的 Code-Server。</p>

<h2>访问方式</h2>
<pre><code>http://{host}<b>/s/{{session_id}}/</b></code></pre>

<h2>当前在线 Session</h2>
<table>
<tr><th>Session ID</th><th>访问地址</th><th>最后活跃</th></tr>
<!-- 动态填充 -->
</table>
<p id="empty" style="color:#999;">暂无在线 Session</p>

<h2>API</h2>
<table>
<tr><th>Endpoint</th><th>说明</th></tr>
<tr><td class="endpoint">GET /__api__/sessions</td><td>列出所有在线 Session</td></tr>
<tr><td class="endpoint">GET /__api__/health</td><td>健康检查</td></tr>
<tr><td class="endpoint">WS  /__tunnel__</td><td>Agent 隧道连接端点</td></tr>
</table>

<script>
fetch('/__api__/sessions')
  .then(r => r.json())
  .then(data => {{
    const tbody = document.querySelector('table tbody') || document.querySelector('table');
    if (data.count === 0) return;
    document.getElementById('empty').style.display = 'none';
    data.sessions.forEach(s => {{
      const host = location.host;
      const url = location.protocol + '//' + host + '/s/' + s.sid + '/';
      const tr = document.createElement('tr');
      tr.innerHTML = '<td><code>' + s.sid + '</code></td>'
        + '<td><a href="' + url + '" target="_blank">' + url + '</a></td>'
        + '<td>' + new Date(s.active_at * 1000).toLocaleString() + '</td>';
      tbody.appendChild(tr);
    }});
  }});
</script>
</body></html>"""
    return web.Response(text=html, content_type="text/html; charset=utf-8")
