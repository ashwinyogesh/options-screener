BASE = r'c:\Users\ashwincha\Options\frontend\src\components'

# ── CspInput.tsx ─────────────────────────────────────────────────────────────
csp_path = BASE + r'\CspInput.tsx'
with open(csp_path, encoding='utf-8') as f:
    csp = f.read()

# 1. Chain Median OI formula: × 15 → × 5
old = '  pts = min(log10(OI) / log10(5000), 1.0) \u00d7 15\n  Log scale gives partial credit for smaller-cap chains.'
new = '  pts = min(log10(OI) / log10(5000), 1.0) \u00d7 5\n  Log scale gives partial credit for smaller-cap chains.'
if old in csp:
    csp = csp.replace(old, new); print('OK  [CSP] Chain OI formula ×15→×5')
else:
    print('MISS[CSP] Chain OI formula')

# 2. Dist vs Support detail: fix wording to match actual backend behaviour
old = "  { factor: 'Dist vs Support', weight: 18,  detail: 'Strike \u2264 support=18 \u00b7 0\u20135% above\u219210 \u00b7 5\u201310%\u21920 \u00b7 >10%=0 \u00b7 all support above strike=+7.',"
new = "  { factor: 'Dist vs Support', weight: 18,  detail: '\u22645% below strike\u219218\u201310 \u00b7 5\u201310% below\u219210\u20130 \u00b7 >10% below=0 \u00b7 all support above strike=7.',"
if old in csp:
    csp = csp.replace(old, new); print('OK  [CSP] Dist vs Support detail')
else:
    print('MISS[CSP] Dist vs Support detail')

# 3. Dist vs Support why: +5 bonus → +7 pts
old = 'confirming the strike is safely below the zone of active participation (+5 bonus).'
new = 'confirming the strike is safely below the zone of active participation (+7 pts).'
if old in csp:
    csp = csp.replace(old, new); print('OK  [CSP] Dist vs Support why +5→+7')
else:
    print('MISS[CSP] Dist vs Support why')

# 4. Dist vs Support formula: +5 → +7
old = '  Bonus: no support below strike but support data exists \u2192 +5 (all support above strike = strong trend)'
new = '  Bonus: no support below strike but support data exists \u2192 +7 (all support above strike = strong uptrend / breakout)'
if old in csp:
    csp = csp.replace(old, new); print('OK  [CSP] Dist vs Support formula +5→+7')
else:
    print('MISS[CSP] Dist vs Support formula')

with open(csp_path, 'w', encoding='utf-8') as f:
    f.write(csp)

# ── CcInput.tsx ──────────────────────────────────────────────────────────────
cc_path = BASE + r'\CcInput.tsx'
with open(cc_path, encoding='utf-8') as f:
    cc = f.read()

# 1. Chain Median OI formula: × 15 → × 5
old = '  pts = min(log10(OI) / log10(5000), 1.0) \u00d7 15'
new = '  pts = min(log10(OI) / log10(5000), 1.0) \u00d7 5'
if old in cc:
    cc = cc.replace(old, new); print('OK  [CC]  Chain OI formula ×15→×5')
else:
    print('MISS[CC]  Chain OI formula')

with open(cc_path, 'w', encoding='utf-8') as f:
    f.write(cc)

print('Done.')
