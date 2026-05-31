import re
from pathlib import Path
art = Path('artifacts/turbo-ocr-raw-5754997-turbo')
form = (art / '01_capture_form.html').read_text(encoding='utf-8')

# Find ALL BDC inputs regardless of format
bdc = re.findall(r'<input[^>]*BDC[^>]*>', form, re.IGNORECASE)
print('=== ALL BDC INPUTS IN CAPTURE FORM ===')
for b in bdc:
    print(b)

# Also find all hidden inputs
hidden = re.findall(r'<input[^>]*type=[\"'\'']hidden[\"'\''][^>]*>', form, re.IGNORECASE)
print('\n=== ALL HIDDEN INPUTS ===')
for h in hidden:
    print(h)

# Check the t= token in img src
tokens = re.findall(r't=([a-f0-9]+)', form)
print('\n=== CAPTCHA TOKENS (t=) ===', tokens)
