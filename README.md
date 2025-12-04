# Transparent Bill Assistant

A prototype tool that helps patients understand confusing medical bills and take action on potential overcharges.

## Features

- **Bill Upload**: Upload PDF or image of medical bills
- **OCR & Parsing**: Extracts text and identifies key amounts (billed, allowed, patient responsibility)
- **Overcharge Detection**: Highlights when billed amounts appear unusually high
- **Action Plan**: Generates phone scripts and email templates for disputing charges

## Quick Start

### Prerequisites

- Python 3.8+
- Tesseract OCR (for image-based PDFs)

```bash
# macOS
brew install tesseract

# Ubuntu
sudo apt-get install tesseract-ocr
```

### Installation

```bash
pip install -r requirements.txt
```

### Run

```bash
python app.py
```

Then open `index.html` in your browser.

## Project Structure

```
billassistant/
├── app.py              # Flask backend (API + parsing logic)
├── index.html          # Frontend (single-page app)
├── requirements.txt    # Python dependencies
└── test_bill_*.pdf     # Sample bills for testing
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/upload_bill` | POST | Upload and parse a bill |
| `/api/decoded_bill` | GET | Get demo bill data |
| `/api/action_plan` | GET | Get dispute templates |
| `/api/wtp` | POST | Log willingness-to-pay |
| `/api/session_event` | POST | Log user events |

## Built for Product Studio

This is a prototype built for the Cornell Tech Product Studio course.
