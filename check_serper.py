"""Quick reachability check. Run after switching VPN nodes:  python3 check_serper.py"""
import pathlib, requests
key = next((l.split("=",1)[1].strip() for l in pathlib.Path(".env").read_text().splitlines()
            if l.startswith("SERPER_API_KEY")), "")
px = "http://127.0.0.1:10808"          # your local proxy; set to "" to test direct
proxies = {"http": px, "https": px} if px else None
try:
    ip = requests.get("http://ip-api.com/json/", proxies=proxies, timeout=15).json()
    print(f"exit IP : {ip.get('query')} / {ip.get('country')} / {ip.get('isp')}")
except Exception as e:
    print("exit IP : (lookup failed)", e)
r = requests.post("https://google.serper.dev/search",
    headers={"X-API-KEY": key, "Content-Type": "application/json"},
    json={"q": "test", "gl": "ir", "hl": "fa"}, proxies=proxies, timeout=25)
ok = r.status_code == 200 and "application/json" in r.headers.get("Content-Type", "")
print(f"serper  : HTTP {r.status_code} -> {'✅ WORKS' if ok else '❌ blocked (try another node)'}")
