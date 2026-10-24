# Z1 smoke tests

Tiny files to find out what Makera Studio accepts. Every move stays at least
10 mm above work zero; a/b/c never start the spindle.

| File | Tests |
|---|---|
| `a_bare_lf.nc` | plain G-code, LF line endings |
| `b_bare_crlf.nc` | same, Windows CRLF line endings |
| `c_comments_lf.nc` | `( )` comments like pcbcam's headers |
| `d_toolchange_lf.nc` | `M6 T1`, `M3 S12000`, `G4 P3` -- **starts the spindle**, run with nothing in reach |

Each should preview a 40 x 30 mm rectangle starting at X10 Y10.
