updates_csp = [
    (
        "  { factor: 'Chain Median OI', weight: 15,  detail: 'log\u2081\u2080 scale \u00b7 log\u2081\u2080(OI)/log\u2081\u2080(5000) \u00d7 15 \u00b7 capped at 15.',",
        "  { factor: 'Chain Median OI', weight: 5,   detail: 'Circuit-breaker only \u00b7 log\u2081\u2080(OI)/log\u2081\u2080(5000) \u00d7 5 \u00b7 near-always maxed on liquid tickers.',"
    ),
    (
        "  { factor: 'Dist vs Support', weight: 13,  detail: 'Strike \u2264 support=13 \u00b7 0\u20135% above\u21928 \u00b7 5\u201310%\u21920 \u00b7 >10%=0 \u00b7 all support above strike=+5.',",
        "  { factor: 'Dist vs Support', weight: 18,  detail: 'Strike \u2264 support=18 \u00b7 0\u20135% above\u219210 \u00b7 5\u201310%\u21920 \u00b7 >10%=0 \u00b7 all support above strike=+7.',"
    ),
    (
        "  { factor: 'Exp Move Buffer', weight: 15,  detail: '\u22650.2\u03c3 outside=15 \u00b7 0\u20130.2\u03c3\u219210 \u00b7 \u22120.1\u20130\u03c3\u21924 \u00b7 deeper inside=0.',",
        "  { factor: 'Exp Move Buffer', weight: 20,  detail: '\u22650.2\u03c3 outside=20 \u00b7 0\u20130.2\u03c3\u219213 \u00b7 \u22120.1\u20130\u03c3\u21925 \u00b7 deeper inside=0.',"
    ),
    (
        "  { factor: 'Bid-Ask Spread',  weight: 22,  detail: '\u22641%=22 \u00b7 \u22643%\u219215 \u00b7 \u22645%\u21928 \u00b7 \u22648%\u21922 \u00b7 >8%=0.',",
        "  { factor: 'Bid-Ask Spread',  weight: 27,  detail: '\u22641%=27 \u00b7 \u22643%\u219218 \u00b7 \u22645%\u219210 \u00b7 \u22648%\u21922.5 \u00b7 >8%=0.',"
    ),
    (
        "  { factor: 'OI / Volume',      weight: 20,  detail: '\u22651000=20 \u00b7 \u2265500\u219214 \u00b7 \u2265200\u21928 \u00b7 \u2265100\u21920 \u00b7 <100=0.',",
        "  { factor: 'OI / Volume',      weight: 5,   detail: 'Circuit-breaker \u00b7 \u22651000=5 \u00b7 \u2265500\u21923.5 \u00b7 \u2265200\u21922 \u00b7 \u2265100\u21920 \u00b7 <100=0.',"
    ),
]

updates_cc = [
    (
        "  { factor: 'Chain Median OI', weight: 15,  detail: 'log\u2081\u2080 scale \u00b7 log\u2081\u2080(OI)/log\u2081\u2080(5000) \u00d7 15 \u00b7 capped at 15.',",
        "  { factor: 'Chain Median OI', weight: 5,   detail: 'Circuit-breaker only \u00b7 log\u2081\u2080(OI)/log\u2081\u2080(5000) \u00d7 5 \u00b7 near-always maxed on liquid tickers.',"
    ),
    (
        "  { factor: 'Dist vs Resistance', weight: 13,  detail: 'Strike \u2265 nearest resistance=13 \u00b7 +5 if all R below strike \u00b7 0\u20135% below\u21928 \u00b7 5\u201310%\u21920 \u00b7 >10%=0.',",
        "  { factor: 'Dist vs Resistance', weight: 18,  detail: 'Strike \u2265 nearest resistance=18 \u00b7 +5 if all R below strike \u00b7 0\u20135% below\u219210 \u00b7 5\u201310%\u21920 \u00b7 >10%=0.',"
    ),
    (
        "  { factor: 'Exp Move Buffer', weight: 15,  detail: '\u22650.2\u03c3 above ceiling=15 \u00b7 0\u20130.2\u03c3\u219210 \u00b7 \u22120.1\u20130\u03c3\u21924 \u00b7 deeper inside=0.',",
        "  { factor: 'Exp Move Buffer', weight: 20,  detail: '\u22650.2\u03c3 above ceiling=20 \u00b7 0\u20130.2\u03c3\u219213 \u00b7 \u22120.1\u20130\u03c3\u21925 \u00b7 deeper inside=0.',"
    ),
    (
        "  { factor: 'Bid-Ask Spread',  weight: 22,  detail: '\u22641%=22 \u00b7 \u22643%\u219215 \u00b7 \u22645%\u21928 \u00b7 \u22648%\u21922 \u00b7 >8%=0.',",
        "  { factor: 'Bid-Ask Spread',  weight: 27,  detail: '\u22641%=27 \u00b7 \u22643%\u219218 \u00b7 \u22645%\u219210 \u00b7 \u22648%\u21922.5 \u00b7 >8%=0.',"
    ),
    (
        "  { factor: 'OI / Volume',      weight: 20,  detail: '\u22651000=20 \u00b7 \u2265500\u219214 \u00b7 \u2265200\u21928 \u00b7 \u2265100\u21920 \u00b7 <100=0.',",
        "  { factor: 'OI / Volume',      weight: 5,   detail: 'Circuit-breaker \u00b7 \u22651000=5 \u00b7 \u2265500\u21923.5 \u00b7 \u2265200\u21922 \u00b7 \u2265100\u21920 \u00b7 <100=0.',"
    ),
]

BASE = r'c:\Users\ashwincha\Options\frontend\src\components'

for fname, upds in [('CspInput.tsx', updates_csp), ('CcInput.tsx', updates_cc)]:
    path = BASE + '\\' + fname
    with open(path, encoding='utf-8') as f:
        content = f.read()
    for old, new in upds:
        if old in content:
            content = content.replace(old, new)
            print(f'OK  [{fname}] {old[:50]}')
        else:
            print(f'MISS[{fname}] {old[:50]}')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

print('Done.')
