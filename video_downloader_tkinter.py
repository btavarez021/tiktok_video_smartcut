import logging
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
import os
import io
import tkinter as tk
from tkinter import filedialog, simpledialog
from googleapiclient.http import MediaIoBaseDownload

# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)

# ------------------------------
# Scopes
# ------------------------------
SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/drive.metadata.readonly"]

# ------------------------------
# Functions
# ------------------------------

def get_credentials(token_file="token.json", credentials_file="credentials.json"):
    """Load credentials, refresh or run OAuth flow if needed."""
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            logging.info("Access token refreshed.")
        else:
            # Use run_local_server which opens a browser for authentication
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_file, SCOPES
            )
            creds = flow.run_local_server(port=0)
            with open(token_file, "w") as token:
                token.write(creds.to_json())
            logging.info(f"Token saved to {token_file}")
    return creds

def get_folder_id(service, folder_name):
    """Find the folder ID by name in My Drive."""
    # Updated query to only look for non-trashed folders with a specific name
    query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
    results = service.files().list(
        q=query,
        spaces='drive',
        fields="files(id, name)"
    ).execute()
    folders = results.get("files", [])
    if not folders:
        logging.error(f"Folder '{folder_name}' not found.")
        return None
    # Use the first found folder
    folder_id = folders[0]['id']
    logging.info(f"Found folder '{folder_name}' with ID: {folder_id}")
    return folder_id

def list_videos_in_folder(service, folder_id):
    """List all video files inside a folder."""
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

    logging.info(f"Found {len(files)} video files in the folder.")
    return files

def download_file(service, file_id, destination_path):
    """Download a file from Google Drive to local path, avoiding duplicates."""
    base, ext = os.path.splitext(destination_path)
    counter = 1
    new_path = destination_path

    while os.path.exists(new_path):
        new_path = f"{base}_{counter}{ext}"
        counter += 1

    logging.info(f"Starting download to {new_path}")
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(new_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        print(f"Download {int(status.progress() * 100)}% complete.")
    logging.info(f"Successfully downloaded file to {new_path}")

    return new_path

# --- New GUI Functions ---

def get_local_directory_gui():
    """Opens a dialog for the user to select a folder."""
    # Initialize Tkinter
    root = tk.Tk()
    root.withdraw() # Hide the main window
    folder_path = filedialog.askdirectory(title="Select a local folder to save the videos")
    root.destroy() # Close the Tkinter instance after selection
    return folder_path

def get_drive_folder_name_gui():
    """Opens a dialog for the user to enter the Drive folder name."""
    root = tk.Tk()
    root.withdraw()
    folder_name = simpledialog.askstring("Input", "Enter the exact name of the Google Drive folder:", parent=root)
    root.destroy()
    return folder_name

# ------------------------------
# Main logic
# ------------------------------
def download_videos_main():
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    # 1. Dynamically get Google Drive folder name using GUI input
    drive_folder_name = get_drive_folder_name_gui()
    if not drive_folder_name:
        logging.error("No Google Drive folder name provided. Exiting.")
        return []

    # 2. Dynamically get the local drop-off directory using a GUI folder selector
    local_folder_path = get_local_directory_gui()
    if not local_folder_path:
        logging.error("No local download directory selected. Exiting.")
        return []

    print(f"\nSearching for Google Drive folder: '{drive_folder_name}'...")
    folder_id = get_folder_id(service, drive_folder_name)

    downloaded_files = []

    if folder_id:
        video_files = list_videos_in_folder(service, folder_id)

        if not video_files:
            logging.info("No video files found in the specified folder.")
            return []

        logging.info(f"Starting downloads to: {local_folder_path}")

        for f in video_files:
            print(f"\nProcessing: {f['name']}")
            destination = os.path.join(local_folder_path, f['name'])
            actual_saved_path = download_file(service, f['id'], destination)
            downloaded_files.append(actual_saved_path)

        logging.info("All downloads complete!")

    return downloaded_files

download_videos_main()
