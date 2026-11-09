# PowerPoint desktop · 2026-11-09 10:07

The brief's PowerPoint edits made and saved by Microsoft PowerPoint itself (through its automation interface), the
file uploaded back through the API, on a new memo per firm.

| | corriemoor | lumen | merrow |
|---|---|---|---|
| Checks as expected | 15/15 | 14/14 | 15/15 |
| Boxes nobody edited unchanged after PowerPoint saved the file | 132/132 | 34/34 | 170/170 |
| Shapes whose identity PowerPoint kept | 107/107 | 37/37 | 100/100 |

## Each check

**corriemoor**
- ✓ PowerPoint desktop made the edits and saved the file: saved — *text,figure,move,picture_free,picture_template,chart_picture,duplicate_free,duplicate_once*
- ✓ the file PowerPoint saved comes back as this memo's: bring_back — *HTTP 200*
- ✓ a box's text rewritten: applied
- ✓ the new text is the memo's: yes
- ✓ a figure typed over: refused
- ✓ the figure unchanged: yes
- ✓ a box the firm places moved: refused
- ✓ the free slide duplicated: applied
- ✓ a slide allowed once duplicated (Cover): refused
- ✓ a picture inserted on the free slide: applied
- ✓ that picture is a pending to confirm: yes
- ✓ a picture inserted on a template slide: kept_aside
- ✓ a chart pasted as a picture: kept_aside
- ✓ boxes nobody edited unchanged after PowerPoint saved the file: 132/132
- ✓ every shape keeps its identity in the saved file: 107/107

**lumen**
- ✓ PowerPoint desktop made the edits and saved the file: saved — *text,figure,move,picture_free,picture_template,duplicate_free,duplicate_once*
- ✓ the file PowerPoint saved comes back as this memo's: bring_back — *HTTP 200*
- ✓ a box's text rewritten: applied
- ✓ the new text is the memo's: yes
- ✓ a figure typed over: refused
- ✓ the figure unchanged: yes
- ✓ a box the firm places moved: refused
- ✓ the free slide duplicated: applied
- ✓ a slide allowed once duplicated (Decision on a page): refused
- ✓ a picture inserted on the free slide: applied
- ✓ that picture is a pending to confirm: yes
- ✓ a picture inserted on a template slide: kept_aside
- ✓ boxes nobody edited unchanged after PowerPoint saved the file: 34/34
- ✓ every shape keeps its identity in the saved file: 37/37

**merrow**
- ✓ PowerPoint desktop made the edits and saved the file: saved — *text,figure,move,picture_free,picture_template,chart_picture,duplicate_free,duplicate_once*
- ✓ the file PowerPoint saved comes back as this memo's: bring_back — *HTTP 200*
- ✓ a box's text rewritten: applied
- ✓ the new text is the memo's: yes
- ✓ a figure typed over: refused
- ✓ the figure unchanged: yes
- ✓ a box the firm places moved: refused
- ✓ the free slide duplicated: applied
- ✓ a slide allowed once duplicated (Cover): refused
- ✓ a picture inserted on the free slide: applied
- ✓ that picture is a pending to confirm: yes
- ✓ a picture inserted on a template slide: kept_aside
- ✓ a chart pasted as a picture: kept_aside
- ✓ boxes nobody edited unchanged after PowerPoint saved the file: 170/170
- ✓ every shape keeps its identity in the saved file: 100/100

## Where it breaks

Nothing broke.
