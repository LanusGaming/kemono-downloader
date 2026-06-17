import logging
from mega import Mega

logger = logging.getLogger("downloader")

def test_mega():
    mega = Mega()
    m = mega.login()  # anonymous login for public links

    public_url = "https://mega.nz/file/7CJ2nLxR#noFjPNiox_fONILEkT1HrqnxOewNSDzcZFnsS34sMbA"
    out_path = "/data/test.zip"

    # download returns the local filename path or raises on error
    local_file = m.download_url(public_url, dest_filename=out_path)
    logger.info("Saved to", local_file)
