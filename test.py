import requests
import os

def main(file_url):

    mineru_api = "http://10.40.88.55:31838/parser/v1/parser"

    with requests.get(file_url, stream=True) as r:
        r.raise_for_status()
        # 直接使用 response.raw 作为文件对象上传
        # 注意：需要正确设置 filename 和 content_type
        files = {
            'file': (file_url.split('/')[-1], r.raw, 'application/pdf')
        }
        upload_resp = requests.post(mineru_api, files=files)

        upload_resp.raise_for_status()
        upload_resp.json()
        data = upload_resp.json().get("data", {}).get("content_list.json")
        
    return {"result": data }

if __name__ == "__main__":
    test_url = "http://10.40.88.55:31000/multi-file/assets/c2172cfd04a9a992b655/f7dae4435958b8d4.pdf"
    result = main(test_url)
    print(result)