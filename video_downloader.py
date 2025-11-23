import logging
import os
import io

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaIoBaseDownload

# ------------------------------
# Logging
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ------------------------------
# OAuth Scopes
# ------------------------------
SCOPES = [
    "https://na01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.readonly&data=05%7C02%7C%7C296954d5592347cd0f9408de2a3716b9%7C84df9e7fe9f640afb435aaaaaaaaaaaa%7C1%7C0%7C638994613263842597%7CUnknown%7CTWFpbGZsb3d8eyJFbXB0eU1hcGkiOnRydWUsIlYiOiIwLjAuMDAwMCIsIlAiOiJXaW4zMiIsIkFOIjoiTWFpbCIsIldUIjoyfQ%3D%3D%7C0%7C%7C%7C&sdata=Y0Eu8QLax2vQ%2FfEeI14ou13bfosCwYhREHqNlIBgGo8%3D&reserved=0",
    "https://na01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.metadata.readonly&data=05%7C02%7C%7C296954d5592347cd0f9408de2a3716b9%7C84df9e7fe9f640afb435aaaaaaaaaaaa%7C1%7C0%7C638994613263870528%7CUnknown%7CTWFpbGZsb3d8eyJFbXB0eU1hcGkiOnRydWUsIlYiOiIwLjAuMDAwMCIsIlAiOiJXaW4zMiIsIkFOIjoiTWFpbCIsIldUIjoyfQ%3D%3D%7C0%7C%7C%7C&sdata=3UFDayrTkyzdzEyXFYVRYkRP%2Bo8N2He7fCEyA41S0lc%3D&reserved=0"
]

# ------------------------------
# 1. Get Credentials (no GUI)
# ------------------------------
def get_credentials(token_file="token.json", credentials_file="credentials.json"):
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            logging.info("✅ Access token refreshed.")
        else:
            # Still opens browser ONCE on initial auth
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)

            with open(token_file, "w") as token:
                token.write(creds.to_json())
            logging.info(f"✅ Token saved to {token_file}")

    return creds

# ------------------------------
# 2. Lookup Folder ID by Name
# ------------------------------
def get_folder_id(service, folder_name):
    query = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and name='{folder_name}' and trashed=false"
    )

    results = service.files().list(
        q=query,
        spaces='drive',
        fields="files(id, name)"
    ).execute()

    folders = results.get("files", [])
    if not folders:
        logging.error(f"❌ Folder '{folder_name}' not found.")
        return None

    folder_id = folders[0]['id']
    logging.info(f"✅ Found folder '{folder_name}' with ID: {folder_id}")
    return folder_id

# ------------------------------
# 3. List Videos in Folder
# ------------------------------
def list_videos_in_folder(service, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed=false"
    files = []
    page_token = None

    while True:
        results = service.files().list(
            q=query,
            spaces='drive',
            pageSize=100,
            pageToken=page_token,
            fields="nextPageToken, files(id, name, mimeType)"
        ).execute()

        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    logging.info(f"🎥 Found {len(files)} video(s) in Google Drive folder.")
    return files

# ------------------------------
# 4. Download File to tik_tok_downloads/
# ------------------------------
def download_file(service, file_id, filename):
    os.makedirs("tik_tok_downloads", exist_ok=True)
    destination_path = os.path.join("tik_tok_downloads", filename)

    base, ext = os.path.splitext(destination_path)
    counter = 1
    new_path = destination_path

    while os.path.exists(new_path):
        new_path = f"{base}_{counter}{ext}"
        counter += 1

    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(new_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False

    while not done:
        status, done = downloader.next_chunk()
        logging.info(f"⬇ Download {int(status.progress() * 100)}%")

    logging.info(f"✅ Saved to {new_path}")
    return new_path

# ------------------------------
# 5. Main function (no GUI)
# ------------------------------
def download_videos_from_drive(folder_name: str):
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    folder_id = get_folder_id(service, folder_name)
    if not folder_id:
        return []

    video_files = list_videos_in_folder(service, folder_id)
    if not video_files:
        logging.info("❌ No videos found.")
        return []

    saved_files = []
    for f in video_files:
        logging.info(f"📥 Downloading: {f['name']}")
        saved_files.append(download_file(service, f['id'], f['name']))

    logging.info("✅ All downloads complete.")
    return saved_files


# If run manually:
if __name__ == "__main__":
    download_videos_from_drive("TikTok Videos")
