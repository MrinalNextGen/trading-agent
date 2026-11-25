/**
 * Google Sheets "web-app link" receiver — lets the Streamlit Live page write to your
 * sheet WITHOUT a service_account.json file.
 *
 * Setup (one time, ~2 minutes):
 *  1. Open your Google Sheet → Extensions → Apps Script. Paste this whole file.
 *  2. Change SHARED_TOKEN below to any random word, and put the same word in .env:
 *       GOOGLE_SHEETS_WEBAPP_TOKEN=that-word
 *  3. Deploy → New deployment → type "Web app"
 *       Execute as: Me      Who has access: Anyone
 *  4. Copy the Web app URL (ends in /exec) into .env:
 *       GOOGLE_SHEETS_WEBAPP_URL=https://script.google.com/macros/s/…/exec
 *
 * Anyone with the URL could post data, which is why the token check is there.
 */
const SHARED_TOKEN = 'change-me';

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (body.token !== SHARED_TOKEN) {
    return ContentService.createTextOutput('forbidden');
  }
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const name = body.sheet || 'Live';
  const sheet = book.getSheetByName(name) || book.insertSheet(name);
  const rows = body.rows || [];
  sheet.clearContents();
  if (rows.length) {
    sheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
    sheet.getRange(1, 1, 1, rows[0].length).setFontWeight('bold');
  }
  sheet.getRange(1, rows.length ? rows[0].length + 2 : 1).setValue('Last sync: ' + new Date().toLocaleString());
  return ContentService.createTextOutput(JSON.stringify({ ok: true, rows: Math.max(rows.length - 1, 0) }))
    .setMimeType(ContentService.MimeType.JSON);
}
