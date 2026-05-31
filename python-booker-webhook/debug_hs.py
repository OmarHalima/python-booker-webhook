import re
from pathlib import Path
art = Path('artifacts/turbo-ocr-raw-5754997-turbo')

form = (art / '01_capture_form.html').read_text(encoding='utf-8')
hs_init = re.search(r'name="BDC_Hs_Captcha"[^>]*value="([^"]+)"', form)
vcid_init = re.search(r'name="BDC_VCID_Captcha"[^>]*value="([^"]+)"', form)
sp_init = re.search(r'name="BDC_SP_Captcha"[^>]*value="([^"]+)"', form)
print('=== CAPTURE FORM (before type) ===')
print('VCID :', vcid_init.group(1) if vcid_init else 'NOT FOUND')
print('Hs   :', hs_init.group(1) if hs_init else 'NOT FOUND')
print('SP   :', sp_init.group(1) if sp_init else 'NOT FOUND')

for i in range(1, 6):
    resp = (art / f'attempt_{i:02d}_response.html').read_text(encoding='utf-8')
    hs_r = re.search(r'name="BDC_Hs_Captcha"[^>]*value="([^"]+)"', resp)
    vcid_r = re.search(r'name="BDC_VCID_Captcha"[^>]*value="([^"]+)"', resp)
    img_r = re.search(r't=([a-f0-9]{32})', resp)
    print(f'\n=== attempt_{i:02d}_response ===')
    print('VCID :', vcid_r.group(1) if vcid_r else 'NOT FOUND')
    print('Hs   :', hs_r.group(1) if hs_r else 'NOT FOUND')
    print('Token:', img_r.group(1) if img_r else 'NOT FOUND')
