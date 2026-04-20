import os
import subprocess
import zipfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
raw_dir=Path("data/raw")
kaggle_dataset=os.getenv("KAGGLE_DATASET","olistbr/brazilian-ecommerce")

def run_command(cmd:list[str])->None:
    result=subprocess.run(cmd,capture_output=True,text=True)
    if result.returncode!=0:
        raise RuntimeError(
            f"Command failed:{' '.join(cmd)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

def clean_raw_dir()->None:
    raw_dir.mkdir(parents=True,exist_ok=True)
    for f in raw_dir.glob("*"):
        if f.is_file():
            f.unlink()

def download_kaggle_data():
    cmd=["kaggle","datasets","download","-d",kaggle_dataset,"-p",str(raw_dir)]
    run_command(cmd)
    zips=list(raw_dir.glob("*.zip"))
    if not zips:
        raise FileNotFoundError("No zip file found")
    zips.sort(key=lambda p:p.stat().st_mtime,reverse=True)
    return zips[0]

def extract_zip(zip_path):
    with zipfile.ZipFile(zip_path,"r") as zf:
        zf.extractall(raw_dir)

def validate_csvs():
    csvs=sorted(raw_dir.glob("*.csv"))
    if not csvs:
        raise FileNotFoundError("No CSVs found")
    print("Downloaded the CSVs")
    for f in csvs:
        print(f'-{f.name}')
    print(f"Total CSV files: {len(csvs)}")
def main():
    if not os.getenv("KAGGLE_USERNAME") or not os.getenv("KAGGLE_KEY"):
        raise ValueError("Missing KAGGLE_USERNAME and/or KAGGLE_KEY in environment")
    print("1.Cleaning raw directory")
    clean_raw_dir()
    print("2.Downloading dataset from kaggle")
    zip_path=download_kaggle_data()
    print("Extracting and then validating CSVs")
    extract_zip(zip_path)
    validate_csvs()
    zip_path.unlink(missing_ok=True)
    print("Kaggle Ingestion comleted")
if __name__ == "__main__": 
    main()
