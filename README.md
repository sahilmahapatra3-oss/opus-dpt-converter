# Bruker OPUS to DPT web converter

This is a Streamlit web interface based on the conversion logic in RyanMan56's `Bruker-OPUS-to-.DPT-bulk-file-converter` repository.

## Files
- `app.py`: browser-based upload, conversion, error reporting, and ZIP download interface.
- `requirements.txt`: Python dependencies installed by Streamlit Community Cloud.

## Run locally
```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Important scientific limitation
The original script uses a manually exported `.dpt` file for the X-axis and assumes it matches every OPUS file. This app still requires such a template and checks only that the number of X values matches the number of `ScSm` intensity values. The user must verify that the template's spectral range and sampling grid really match the uploaded OPUS files.

The app extracts `ScSm` and writes `wavenumber,intensity` rows with five decimal places using decimal `ROUND_HALF_EVEN`, consistent with the original script. It does not automatically detect or choose other OPUS spectrum types.
