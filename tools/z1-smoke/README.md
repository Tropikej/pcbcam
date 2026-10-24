# Z1 smoke tests

Tiny files to find out what Makera Studio accepts. Every move stays at least
10 mm above work zero. Each should preview a 40 x 30 mm rectangle at X10 Y10.

| File | Tests |
|---|---|
| `a_bare_lf.cnc` | plain G-code, `.cnc`, LF line endings |
| `b_bare_crlf.cnc` | same, CRLF line endings |
| `c_bare_crlf.nc` | same as b but `.nc` -- expected to fail if the extension matters |
| `e_fusion_style.cnc` | exact structure of Makera's Fusion post (T1M6, M851, G54, G28, M30) -- **starts the spindle** |

Result on 2026-10-24 with the earlier `.nc` set: no preview at all, even for plain G-code.
