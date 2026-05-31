import re
from pathlib import Path
art = Path('artifacts/turbo-ocr-raw-5754997-turbo')

form = (art / '01_capture_form.html').read_text(encoding='utf-8')
vcid = re.findall(r'BDC_VCID_Captcha[^\"]*value=\"([^\"]+)\"', form)
img_src = re.findall(r'Captcha_CaptchaImage[^>]*src=\"([^\"]+)\"', form)
hs = re.findall(r'BDC_Hs_Captcha[^\"]*value=\"([^\"]+)\"', form)
print('=== CAPTURE FORM ===')
print('VCID:', vcid)
print('Img src:', img_src)
print('Hs_initial:', hs)

for i in range(1, 6):
    resp = (art / f'attempt_{i:02d}_response.html').read_text(encoding='utf-8')
    vcid_r = re.findall(r'BDC_VCID_Captcha[^\"]*value=\"([^\"]+)\"', resp)
    img_r = re.findall(r'Captcha_CaptchaImage[^>]*src=\"([^\"]+)\"', resp)
    hs_r = re.findall(r'BDC_Hs_Captcha[^\"]*value=\"([^\"]+)\"', resp)
    print(f'\n=== attempt_{i:02d}_response ===')
    print('VCID:', vcid_r)
    print('Img src:', img_r)
    print('Hs:', hs_r)
