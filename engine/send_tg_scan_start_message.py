import requests
import json
import csv
import os
import re
import time
import sys

def format_message(domain):
    def escape_html(text):
        return re.sub(r"[<>&]", lambda x: {"<": "&lt;", ">": "&gt;", "&": "&amp;"}[x.group()], text)
    message+=f'#Start scanning ... {domain}'


    return message



def send_telegram_message(message):
    token = os.getenv('TELEGRAM_BOT')
    chat_id = os.getenv('TELEGRAM_GROUP')
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None





def main():
    send_telegram_message(format_message(sys.argv[1]))




if __name__ == "__main__":
    main()