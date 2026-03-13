/*
 * clu hardware — Claude usage monitor for M5StickC Plus2
 *
 * Polls your Mac running `clu --serve` and displays the bouncing
 * clu mascot + 5h/7d usage bars on the built-in TFT.
 *
 * Setup:
 *   1. Board: M5Stick-C-Plus2 (install via Board Manager → M5Stack)
 *   2. Libraries: M5StickCPlus2, ArduinoJson (v7)
 *   3. Fill in WIFI_SSID, WIFI_PASS, SERVER_IP below
 *   4. On your Mac: clu --serve   (or clu --serve --port 8765)
 *   5. Flash and enjoy
 */

#include <M5StickCPlus2.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ─── Config ───────────────────────────────────────────────────────────────────
const char* WIFI_SSID    = "YOUR_WIFI_SSID";
const char* WIFI_PASS    = "YOUR_WIFI_PASS";
const char* SERVER_IP    = "192.168.1.X";   // Mac's local IP (printed by clu --serve)
const int   SERVER_PORT  = 8765;
const int   REFRESH_MS   = 90000;           // 90 seconds

// ─── RGB565 color palette (matches clu terminal colors) ───────────────────────
#define CLR_BG      0x0000  // black
#define CLR_AMBER   0xDBA0  // #d97706
#define CLR_VIOLET  0xA45F  // #a78bfa
#define CLR_CYAN    0x675F  // #67e8f9
#define CLR_GREEN   0x3693  // #34d399
#define CLR_ORANGE  0xFC87  // #fb923c
#define CLR_RED     0xFB8E  // #f87171
#define CLR_WHITE   0xF7BE  // #f3f4f6
#define CLR_MUTED   0x6B90  // #6b7280
#define CLR_DIM     0x320A  // #374151
#define CLR_SKIN    0xCC2D  // #c8866b

// ─── State ────────────────────────────────────────────────────────────────────
float  pct_5h       = -1, pct_7d = -1;
long   reset_5h_s   = -1, reset_7d_s = -1;   // seconds until reset at last fetch
long   tokens_5h    = 0;
bool   has_error    = false;
String error_msg    = "";

int           tick          = 0;
unsigned long last_fetch_ms = 0;
unsigned long fetch_at_ms   = 0;  // millis() when last fetch happened

// Screen layout (landscape 240×135)
//   Mascot : x 0..69,  full height
//   Divider: x 70
//   Stats  : x 75..235
#define MASCOT_CX  35    // mascot center X
#define MASCOT_BY  25    // mascot head base Y (before bounce)
#define STATS_X    75    // stats panel left edge

// ─── Helpers ──────────────────────────────────────────────────────────────────
uint16_t pct_color(float pct) {
  if (pct >= 90) return CLR_RED;
  if (pct >= 70) return CLR_ORANGE;
  if (pct >= 40) return CLR_AMBER;
  return CLR_GREEN;
}

String fmt_secs(long secs) {
  if (secs < 0) return "--";
  if (secs == 0) return "now";
  long d = secs / 86400;
  long h = (secs % 86400) / 3600;
  long m = (secs % 3600) / 60;
  char buf[16];
  if (d > 0)      snprintf(buf, sizeof(buf), "%ldd %ldh", d, h);
  else if (h > 0) snprintf(buf, sizeof(buf), "%ldh %02ldm", h, m);
  else            snprintf(buf, sizeof(buf), "%ldm", m);
  return String(buf);
}

String fmt_tokens(long n) {
  char buf[12];
  if (n >= 1000000) snprintf(buf, sizeof(buf), "%.1fM", n / 1000000.0f);
  else if (n >= 1000) snprintf(buf, sizeof(buf), "%.1fK", n / 1000.0f);
  else snprintf(buf, sizeof(buf), "%ld", n);
  return String(buf);
}

// ─── Mascot drawing ───────────────────────────────────────────────────────────
// Bounce: every BOUNCE_INTERVAL ticks, the mascot jumps up 4 frames
#define BOUNCE_INTERVAL 120
#define BOUNCE_TICKS_PER_FRAME 3

int bounce_offset(int t) {
  int pos = t % BOUNCE_INTERVAL;
  if (pos < BOUNCE_TICKS_PER_FRAME * 4) {
    int frame = pos / BOUNCE_TICKS_PER_FRAME;
    int offs[] = {-5, -8, -5, 0};
    return offs[frame];
  }
  return 0;
}

bool is_blink(int t) {
  return (t % 20) < 2;
}

void draw_mascot(int cx, int base_y, int t) {
  int yo = bounce_offset(t);
  int y  = base_y + yo;

  // Clear mascot area
  M5.Lcd.fillRect(0, 0, 70, 135, CLR_BG);

  // ── Antenna tip (violet dot) ──
  M5.Lcd.fillCircle(cx, y - 20, 2, CLR_VIOLET);

  // ── Antenna stick ──
  M5.Lcd.drawLine(cx, y - 17, cx, y - 6, CLR_VIOLET);

  // ── Head outline ──
  M5.Lcd.drawRoundRect(cx - 14, y - 4, 28, 22, 3, CLR_SKIN);

  // ── Eyes ──
  if (is_blink(t)) {
    // ^ ^ blink
    M5.Lcd.drawLine(cx - 10, y + 8, cx - 7, y + 5, CLR_VIOLET);
    M5.Lcd.drawLine(cx -  7, y + 5, cx - 4, y + 8, CLR_VIOLET);
    M5.Lcd.drawLine(cx +  4, y + 8, cx + 7, y + 5, CLR_VIOLET);
    M5.Lcd.drawLine(cx +  7, y + 5, cx + 10, y + 8, CLR_VIOLET);
  } else {
    int style = (t / 40) % 4;
    switch (style) {
      case 0: // ◕ filled
        M5.Lcd.fillCircle(cx - 7, y + 7, 3, CLR_VIOLET);
        M5.Lcd.fillCircle(cx + 7, y + 7, 3, CLR_VIOLET);
        break;
      case 1: // ● smaller dot
        M5.Lcd.fillCircle(cx - 7, y + 7, 2, CLR_VIOLET);
        M5.Lcd.fillCircle(cx + 7, y + 7, 2, CLR_VIOLET);
        break;
      case 2: // ○ hollow
        M5.Lcd.drawCircle(cx - 7, y + 7, 3, CLR_VIOLET);
        M5.Lcd.drawCircle(cx + 7, y + 7, 3, CLR_VIOLET);
        break;
      case 3: // · dots
        M5.Lcd.fillCircle(cx - 7, y + 7, 1, CLR_VIOLET);
        M5.Lcd.fillCircle(cx + 7, y + 7, 1, CLR_VIOLET);
        break;
    }
  }

  // ── Chin (bottom of head with notch gaps) ──
  int chin_y = y + 18;
  M5.Lcd.drawLine(cx - 14, chin_y, cx - 5, chin_y, CLR_SKIN);  // left side
  M5.Lcd.drawLine(cx +  5, chin_y, cx + 14, chin_y, CLR_SKIN); // right side
  // notch connectors going down
  M5.Lcd.drawLine(cx - 5, chin_y, cx - 5, chin_y + 4, CLR_SKIN);
  M5.Lcd.drawLine(cx + 5, chin_y, cx + 5, chin_y + 4, CLR_SKIN);

  // ── Legs ──
  int leg_top = chin_y + 4;
  int leg_bot = leg_top + 20;
  M5.Lcd.drawLine(cx - 5, leg_top, cx - 5, leg_bot, CLR_SKIN);
  M5.Lcd.drawLine(cx + 5, leg_top, cx + 5, leg_bot, CLR_SKIN);

  // ── Vertical divider ──
  M5.Lcd.drawLine(70, 8, 70, 127, CLR_DIM);
}

// ─── Stats panel ──────────────────────────────────────────────────────────────
void draw_stats() {
  M5.Lcd.fillRect(STATS_X, 0, 240 - STATS_X, 135, CLR_BG);

  // Compute current reset countdowns (local countdown since last fetch)
  long elapsed_s = (long)((millis() - fetch_at_ms) / 1000);
  long cur_5h = (reset_5h_s >= 0) ? max(0L, reset_5h_s - elapsed_s) : -1;
  long cur_7d = (reset_7d_s >= 0) ? max(0L, reset_7d_s - elapsed_s) : -1;

  int x  = STATS_X;
  int y  = 10;
  int bw = 155;   // bar width px
  int bh = 7;     // bar height px

  // ── 5H row ──
  uint16_t c5 = (pct_5h >= 0) ? pct_color(pct_5h) : CLR_MUTED;

  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(CLR_AMBER, CLR_BG);
  M5.Lcd.setCursor(x, y);
  M5.Lcd.print("5H");

  if (pct_5h >= 0) {
    char buf[8];
    snprintf(buf, sizeof(buf), " %3.0f%%", pct_5h);
    M5.Lcd.setTextColor(c5, CLR_BG);
    M5.Lcd.print(buf);

    // progress bar
    int filled = (int)(pct_5h / 100.0f * bw);
    M5.Lcd.fillRect(x, y + 10, filled, bh, c5);
    M5.Lcd.fillRect(x + filled, y + 10, bw - filled, bh, CLR_DIM);
  } else {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.print(" --");
  }

  M5.Lcd.setTextColor(CLR_CYAN, CLR_BG);
  M5.Lcd.setCursor(x, y + 20);
  M5.Lcd.print("reset ");
  M5.Lcd.print(fmt_secs(cur_5h));

  // ── 7D row ──
  y += 42;
  uint16_t c7 = (pct_7d >= 0) ? pct_color(pct_7d) : CLR_MUTED;

  M5.Lcd.setTextColor(CLR_VIOLET, CLR_BG);
  M5.Lcd.setCursor(x, y);
  M5.Lcd.print("7D");

  if (pct_7d >= 0) {
    char buf[8];
    snprintf(buf, sizeof(buf), " %3.0f%%", pct_7d);
    M5.Lcd.setTextColor(c7, CLR_BG);
    M5.Lcd.print(buf);

    int filled = (int)(pct_7d / 100.0f * bw);
    M5.Lcd.fillRect(x, y + 10, filled, bh, c7);
    M5.Lcd.fillRect(x + filled, y + 10, bw - filled, bh, CLR_DIM);
  } else {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.print(" --");
  }

  M5.Lcd.setTextColor(CLR_CYAN, CLR_BG);
  M5.Lcd.setCursor(x, y + 20);
  M5.Lcd.print("reset ");
  M5.Lcd.print(fmt_secs(cur_7d));

  // ── Token count ──
  y += 42;
  if (tokens_5h > 0) {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("◈ ");
    M5.Lcd.setTextColor(CLR_WHITE, CLR_BG);
    M5.Lcd.print(fmt_tokens(tokens_5h));
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.print(" tok 5h");
  }

  // ── Error banner ──
  if (has_error) {
    M5.Lcd.setTextColor(CLR_RED, CLR_BG);
    M5.Lcd.setCursor(x, 122);
    M5.Lcd.print(error_msg.substring(0, 24));
  }
}

// ─── HTTP fetch ───────────────────────────────────────────────────────────────
void fetch_data() {
  if (WiFi.status() != WL_CONNECTED) {
    has_error = true;
    error_msg = "WiFi lost";
    WiFi.reconnect();
    return;
  }

  HTTPClient http;
  String url = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/api";
  http.begin(url);
  http.setTimeout(8000);
  int code = http.GET();

  if (code == 200) {
    String body = http.getString();
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (!err) {
      pct_5h      = doc["pct_5h"].isNull()       ? -1.0f : (float)doc["pct_5h"];
      pct_7d      = doc["pct_7d"].isNull()       ? -1.0f : (float)doc["pct_7d"];
      reset_5h_s  = doc["reset_5h_secs"].isNull() ? -1L  : (long)doc["reset_5h_secs"];
      reset_7d_s  = doc["reset_7d_secs"].isNull() ? -1L  : (long)doc["reset_7d_secs"];
      tokens_5h   = doc["tokens_5h"]  | 0L;
      fetch_at_ms = millis();

      const char* srv_err = doc["error"];
      has_error = (srv_err && strlen(srv_err) > 0);
      error_msg = has_error ? String(srv_err).substring(0, 24) : "";
    } else {
      has_error = true;
      error_msg = "json err";
    }
  } else {
    has_error = true;
    error_msg = "HTTP " + String(code);
  }
  http.end();
}

// ─── Setup ────────────────────────────────────────────────────────────────────
void setup() {
  M5.begin();
  M5.Lcd.setRotation(1);    // landscape: 240×135
  M5.Lcd.fillScreen(CLR_BG);
  M5.Lcd.setBrightness(80);

  // Connecting splash
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(CLR_AMBER, CLR_BG);
  M5.Lcd.setCursor(10, 55);
  M5.Lcd.print("clu · connecting");

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    M5.Lcd.print(".");
  }

  M5.Lcd.fillScreen(CLR_BG);

  if (WiFi.status() == WL_CONNECTED) {
    fetch_data();
  } else {
    has_error = true;
    error_msg = "WiFi failed";
  }

  draw_stats();
  last_fetch_ms = millis();
}

// ─── Loop ─────────────────────────────────────────────────────────────────────
void loop() {
  M5.update();
  tick++;

  // Animate mascot every ~50ms
  draw_mascot(MASCOT_CX, MASCOT_BY, tick);

  // Refresh stats from server periodically
  unsigned long now = millis();
  if (now - last_fetch_ms >= (unsigned long)REFRESH_MS) {
    fetch_data();
    draw_stats();
    last_fetch_ms = now;
  }

  // Redraw reset countdowns every 10 seconds (local countdown)
  static unsigned long last_countdown = 0;
  if (now - last_countdown >= 10000) {
    draw_stats();
    last_countdown = now;
  }

  delay(50);
}
