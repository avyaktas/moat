import requests

HEADERS = {"User-Agent": "Avyakta Sharma avyaktansharma@gmail.com"}

url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"
response = requests.get(url, headers=HEADERS)

print(response.status_code)
data = response.json()
print(data["entityName"])
print(list(data["facts"]["us-gaap"].keys())[:20])
