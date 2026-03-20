/*
 * clu hardware — Claude usage monitor for M5StickC Plus2
 *
 * Features:
 *   - Bouncing mascot with gravity-tracking eyes (IMU)
 *   - Multi-screen: usage → projects → tokens → promo (Button A)
 *   - Manual refresh (Button B)
 *   - Gentle buzzer alert at 80% usage
 *   - Tilt-to-sleep: flip face-down to dim, pick up to wake
 *
 * Setup:
 *   1. Copy credentials.example.h to credentials.h and fill in your details
 *   2. On your Mac: clu --serve   (or clu --serve --port 8765)
 *   3. Flash via PlatformIO and enjoy
 */

#include <M5StickCPlus2.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "esp_wpa2.h"
#include "credentials.h"

// ─── Config ───────────────────────────────────────────────────────────────────
const int   REFRESH_MS   = 90000;           // 90 seconds
const int   BUZZER_PIN   = 2;               // M5StickC Plus2 buzzer GPIO
const float ALERT_PCT    = 80.0f;           // buzz when 5h hits this

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
#define CLR_GOLD    0xFE60  // #fbbf24

// ─── State ────────────────────────────────────────────────────────────────────
float  pct_5h       = -1, pct_7d = -1;
long   reset_5h_s   = -1, reset_7d_s = -1;
long   tokens_5h    = 0;
float  pace_pct     = -1;
bool   is_promo     = false;
String plan_name    = "";
String promo_label  = "";
bool   has_error    = false;
String error_msg    = "";

// Project data from API
String project_names[8];
long   project_tokens[8];
int    project_count = 0;

// Totals from API
long   total_tokens   = 0;
int    total_messages  = 0;
int    total_projects  = 0;
int    total_sessions  = 0;
float  cache_hit_rate  = 0;

int           tick          = 0;
unsigned long last_fetch_ms = 0;
unsigned long fetch_at_ms   = 0;

// ─── Multi-screen ─────────────────────────────────────────────────────────────
enum Screen { SCR_USAGE, SCR_PROJECTS, SCR_TOKENS, SCR_PROMO };
Screen current_screen = SCR_USAGE;
const int NUM_SCREENS = 4;

// ─── IMU / tilt ───────────────────────────────────────────────────────────────
float imu_ax = 0, imu_ay = 0, imu_az = 0;
bool  is_asleep    = false;
bool  alert_fired  = false;  // only buzz once per crossing

// Screen layout (landscape 240×135)
#define MASCOT_CX  35
#define MASCOT_BY  25
#define STATS_X    75

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

// ─── Buzzer ───────────────────────────────────────────────────────────────────
void soft_chirp() {
  // Gentle: 2 short quiet chirps
  M5.Speaker.tone(1800, 80);   // 1800 Hz, 80ms
  delay(120);
  M5.Speaker.tone(2200, 60);   // slightly higher, 60ms
  delay(80);
  M5.Speaker.end();
}

// ─── IMU read ─────────────────────────────────────────────────────────────────
void read_imu() {
  float gx, gy, gz;
  M5.Imu.getAccel(&imu_ax, &imu_ay, &imu_az);
}

// ─── Mascot drawing (with gravity eyes) ──────────────────────────────────────
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
  // Blink ~every 4-8 seconds, lasts 2 frames
  // Use a simple pseudo-random feel by mixing tick values
  int cycle = t % 80;            // ~20 seconds at 250ms per tick
  if (cycle == 0 || cycle == 1) return true;
  if (cycle == 37 || cycle == 38) return true;  // second blink offset
  return false;
}

void draw_mascot(int cx, int base_y, int t) {
  int yo = bounce_offset(t);
  int y  = base_y + yo;

  M5.Lcd.fillRect(0, 0, 70, 135, CLR_BG);

  // Antenna tip
  M5.Lcd.fillCircle(cx, y - 20, 2, CLR_VIOLET);
  // Antenna stick
  M5.Lcd.drawLine(cx, y - 17, cx, y - 6, CLR_VIOLET);
  // Head outline
  M5.Lcd.drawRoundRect(cx - 14, y - 4, 28, 22, 3, CLR_SKIN);

  // ── Eyes (gravity-tracking) ──
  if (is_blink(t)) {
    // ^ ^ blink
    M5.Lcd.drawLine(cx - 10, y + 8, cx - 7, y + 5, CLR_VIOLET);
    M5.Lcd.drawLine(cx -  7, y + 5, cx - 4, y + 8, CLR_VIOLET);
    M5.Lcd.drawLine(cx +  4, y + 8, cx + 7, y + 5, CLR_VIOLET);
    M5.Lcd.drawLine(cx +  7, y + 5, cx + 10, y + 8, CLR_VIOLET);
  } else {
    // Eye tracking: "look at the user"
    // Neutral = screen vertical (90° from floor), facing user → eyes centered
    // With rotation=1, when held upright: az≈0, ay≈-1 (gravity down screen)
    // Tilt left/right: ax changes → eyes shift horizontally
    // Tilt forward/back: az changes → eyes shift vertically
    //   az > 0 = screen tilted back (looking up) → pupils up
    //   az < 0 = screen tilted forward (looking down) → pupils down
    static float smooth_dx = 0, smooth_dy = 0;
    smooth_dx = smooth_dx * 0.65f + (-imu_ay) * 0.35f;    // tilt left → eyes right
    smooth_dy = smooth_dy * 0.65f + imu_az * 0.35f;     // tilt up → eyes down
    int eye_dx = constrain((int)(smooth_dx * 3.5f), -3, 3);
    int eye_dy = constrain((int)(smooth_dy * 3.5f), -3, 3);

    int lx = cx - 7, rx = cx + 7, ey = y + 7;

    // Eye sockets
    M5.Lcd.drawCircle(lx, ey, 4, CLR_DIM);
    M5.Lcd.drawCircle(rx, ey, 4, CLR_DIM);
    // Pupils track user
    M5.Lcd.fillCircle(lx + eye_dx, ey + eye_dy, 2, CLR_VIOLET);
    M5.Lcd.fillCircle(rx + eye_dx, ey + eye_dy, 2, CLR_VIOLET);
    // Tiny highlight for life
    M5.Lcd.drawPixel(lx + eye_dx - 1, ey + eye_dy - 1, CLR_WHITE);
    M5.Lcd.drawPixel(rx + eye_dx - 1, ey + eye_dy - 1, CLR_WHITE);
  }

  // Chin
  int chin_y = y + 18;
  M5.Lcd.drawLine(cx - 14, chin_y, cx - 5, chin_y, CLR_SKIN);
  M5.Lcd.drawLine(cx +  5, chin_y, cx + 14, chin_y, CLR_SKIN);
  M5.Lcd.drawLine(cx - 5, chin_y, cx - 5, chin_y + 4, CLR_SKIN);
  M5.Lcd.drawLine(cx + 5, chin_y, cx + 5, chin_y + 4, CLR_SKIN);

  // Legs
  int leg_top = chin_y + 4;
  int leg_bot = leg_top + 20;
  M5.Lcd.drawLine(cx - 5, leg_top, cx - 5, leg_bot, CLR_SKIN);
  M5.Lcd.drawLine(cx + 5, leg_top, cx + 5, leg_bot, CLR_SKIN);

  // Battery below legs
  static int bat_pct = -1;
  static bool charging = false;
  static unsigned long last_bat_read = 0;
  static float prev_voltage = 0;
  // Read every 10s, detect charging by voltage trend
  if (bat_pct < 0 || millis() - last_bat_read >= 10000) {
    float bat_v = M5.Power.getBatteryVoltage() / 1000.0f;
    bat_pct = constrain((int)((bat_v - 3.0f) / 1.2f * 100), 0, 100);
    // Charging = voltage rising or near full (>4.1V) with USB power
    charging = (bat_v > prev_voltage + 0.005f) || (bat_v > 4.15f);
    prev_voltage = bat_v;
    last_bat_read = millis();
  }

  uint16_t bat_clr = (bat_pct > 50) ? CLR_GREEN : (bat_pct > 20) ? CLR_AMBER : CLR_RED;

  // Draw battery icon (12x7 px) centered below mascot
  int bx = cx - 6, by = leg_bot + 4;
  M5.Lcd.drawRect(bx, by, 10, 7, bat_clr);           // body
  M5.Lcd.fillRect(bx + 10, by + 2, 2, 3, bat_clr);   // nub
  int fill_w = (int)(bat_pct / 100.0f * 8);
  if (fill_w > 0) M5.Lcd.fillRect(bx + 1, by + 1, fill_w, 5, bat_clr);

  // Charging bolt or percentage below
  char bat_buf[8];
  if (charging) {
    snprintf(bat_buf, sizeof(bat_buf), "%d%%+", bat_pct);
  } else {
    snprintf(bat_buf, sizeof(bat_buf), "%d%%", bat_pct);
  }
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(bat_clr, CLR_BG);
  int bat_w = strlen(bat_buf) * 6;
  M5.Lcd.setCursor(cx - bat_w / 2, by + 10);
  M5.Lcd.print(bat_buf);

  // Divider
  M5.Lcd.drawLine(70, 8, 70, 127, CLR_DIM);
}

// ─── Screen: Usage (main) ────────────────────────────────────────────────────
void draw_screen_usage() {
  M5.Lcd.fillRect(STATS_X, 0, 240 - STATS_X, 135, CLR_BG);

  long elapsed_s = (long)((millis() - fetch_at_ms) / 1000);
  long cur_5h = (reset_5h_s >= 0) ? max(0L, reset_5h_s - elapsed_s) : -1;
  long cur_7d = (reset_7d_s >= 0) ? max(0L, reset_7d_s - elapsed_s) : -1;

  int x = STATS_X, y = 6;
  int bw = 155, bh = 7;

  // ── Promo badge ──
  if (is_promo) {
    M5.Lcd.setTextSize(1);
    M5.Lcd.setTextColor(CLR_BG, CLR_GOLD);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print(" * 2X ");
    M5.Lcd.setTextColor(CLR_GOLD, CLR_BG);
    M5.Lcd.print(" PROMO");
    y += 12;
  }

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
  y += 36;
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

  // ── Pace ──
  y += 36;
  if (pace_pct >= 0) {
    uint16_t pc = (pace_pct > 150) ? CLR_RED : (pace_pct > 100) ? CLR_ORANGE : CLR_GREEN;
    M5.Lcd.setTextColor(pc, CLR_BG);
    M5.Lcd.setCursor(x, y);
    char pbuf[16];
    snprintf(pbuf, sizeof(pbuf), "pace %.0f%%", pace_pct);
    M5.Lcd.print(pbuf);
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.print(pace_pct <= 100 ? " ok" : " fast");
  }

  // ── Plan badge ──
  if (plan_name.length() > 0) {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.setCursor(x, 122);
    M5.Lcd.print(plan_name.substring(0, 24));
  }

  // ── Error ──
  if (has_error) {
    M5.Lcd.setTextColor(CLR_RED, CLR_BG);
    M5.Lcd.setCursor(x, 122);
    M5.Lcd.print(error_msg.substring(0, 24));
  }
}

// ─── Screen: Projects ────────────────────────────────────────────────────────
void draw_screen_projects() {
  M5.Lcd.fillRect(STATS_X, 0, 240 - STATS_X, 135, CLR_BG);
  int x = STATS_X, y = 4;

  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(CLR_AMBER, CLR_BG);
  M5.Lcd.setCursor(x, y);
  M5.Lcd.print("PROJECTS");
  y += 14;

  M5.Lcd.drawLine(x, y, x + 155, y, CLR_DIM);
  y += 4;

  if (project_count == 0) {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("no data");
    return;
  }

  for (int i = 0; i < min(project_count, 7); i++) {
    // Truncate project name to fit
    String name = project_names[i].substring(0, 16);
    M5.Lcd.setTextColor(CLR_WHITE, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print(name);

    // Right-align token count
    String tok = fmt_tokens(project_tokens[i]);
    int tw = tok.length() * 6;  // approx width at text size 1
    M5.Lcd.setTextColor(CLR_CYAN, CLR_BG);
    M5.Lcd.setCursor(x + 155 - tw, y);
    M5.Lcd.print(tok);

    y += 14;
  }
}

// ─── Screen: Tokens (totals) ─────────────────────────────────────────────────
void draw_screen_tokens() {
  M5.Lcd.fillRect(STATS_X, 0, 240 - STATS_X, 135, CLR_BG);
  int x = STATS_X, y = 4;

  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(CLR_AMBER, CLR_BG);
  M5.Lcd.setCursor(x, y);
  M5.Lcd.print("TOTALS");
  y += 14;
  M5.Lcd.drawLine(x, y, x + 155, y, CLR_DIM);
  y += 6;

  // Helper lambda-like rows
  struct Row { const char* label; String value; uint16_t color; };
  Row rows[] = {
    {"Tokens",   fmt_tokens(total_tokens),              CLR_WHITE},
    {"Messages", String(total_messages),                 CLR_CYAN},
    {"Projects", String(total_projects),                 CLR_VIOLET},
    {"Sessions", String(total_sessions),                 CLR_GREEN},
    {"Cache",    String(cache_hit_rate, 0) + "%",        CLR_GREEN},
  };

  for (int i = 0; i < 5; i++) {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print(rows[i].label);

    int vw = rows[i].value.length() * 6;
    M5.Lcd.setTextColor(rows[i].color, CLR_BG);
    M5.Lcd.setCursor(x + 155 - vw, y);
    M5.Lcd.print(rows[i].value);

    y += 16;
  }

  // 5h tokens this window
  y += 4;
  if (tokens_5h > 0) {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("5h win");
    String tw = fmt_tokens(tokens_5h);
    int vw = tw.length() * 6;
    M5.Lcd.setTextColor(CLR_AMBER, CLR_BG);
    M5.Lcd.setCursor(x + 155 - vw, y);
    M5.Lcd.print(tw);
  }
}

// ─── Screen: Promo ───────────────────────────────────────────────────────────
void draw_screen_promo() {
  M5.Lcd.fillRect(STATS_X, 0, 240 - STATS_X, 135, CLR_BG);
  int x = STATS_X, y = 10;

  M5.Lcd.setTextSize(1);

  if (is_promo) {
    // Gold banner
    M5.Lcd.setTextColor(CLR_GOLD, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("* 2X PROMO *");
    y += 16;

    M5.Lcd.setTextColor(CLR_WHITE, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("ACTIVE NOW");
    y += 20;

    if (promo_label.length() > 0) {
      // Word-wrap the promo label
      String remaining = promo_label;
      while (remaining.length() > 0 && y < 120) {
        String line = remaining.substring(0, 24);
        M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
        M5.Lcd.setCursor(x, y);
        M5.Lcd.print(line);
        remaining = remaining.substring(min((unsigned int)24, remaining.length()));
        y += 12;
      }
    }

    // Fun: animate sparkles
    int sparkle_x = x + (tick * 3) % 150;
    int sparkle_y = 8 + (tick * 7) % 12;
    M5.Lcd.fillCircle(sparkle_x, sparkle_y, 1, CLR_GOLD);
  } else {
    M5.Lcd.setTextColor(CLR_MUTED, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("No promo active");
    y += 20;

    M5.Lcd.setTextColor(CLR_DIM, CLR_BG);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("Check back later");
    y += 16;
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print("for 2x events");
  }

  // Plan info at bottom
  if (plan_name.length() > 0) {
    M5.Lcd.setTextColor(CLR_VIOLET, CLR_BG);
    M5.Lcd.setCursor(x, 118);
    M5.Lcd.print(plan_name.substring(0, 24));
  }
}

// ─── Draw current screen ─────────────────────────────────────────────────────
void draw_current_screen() {
  switch (current_screen) {
    case SCR_USAGE:    draw_screen_usage();    break;
    case SCR_PROJECTS: draw_screen_projects(); break;
    case SCR_TOKENS:   draw_screen_tokens();   break;
    case SCR_PROMO:    draw_screen_promo();    break;
  }

  // Screen indicator dots at bottom center of mascot area
  for (int i = 0; i < NUM_SCREENS; i++) {
    int dot_x = 20 + i * 12;
    int dot_y = 130;
    if (i == (int)current_screen) {
      M5.Lcd.fillCircle(dot_x, dot_y, 2, CLR_AMBER);
    } else {
      M5.Lcd.fillCircle(dot_x, dot_y, 1, CLR_DIM);
    }
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
  String url = String("http://") + SERVER_IP + ":" + String(SERVER_PORT) + "/api";
  http.begin(url);
  http.setTimeout(8000);
  int code = http.GET();

  if (code == 200) {
    String body = http.getString();
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (!err) {
      float old_5h = pct_5h;

      pct_5h      = doc["pct_5h"].isNull()       ? -1.0f : (float)doc["pct_5h"];
      pct_7d      = doc["pct_7d"].isNull()       ? -1.0f : (float)doc["pct_7d"];
      reset_5h_s  = doc["reset_5h_secs"].isNull() ? -1L  : (long)doc["reset_5h_secs"];
      reset_7d_s  = doc["reset_7d_secs"].isNull() ? -1L  : (long)doc["reset_7d_secs"];
      tokens_5h   = doc["tokens_5h"]  | 0L;
      pace_pct    = doc["pace_pct"].isNull()      ? -1.0f : (float)doc["pace_pct"];
      is_promo    = doc["is_promo"] | false;
      fetch_at_ms = millis();

      // Plan
      const char* p = doc["plan"];
      plan_name = p ? String(p) : "";

      // Promo label
      const char* pl = doc["promo_label"];
      promo_label = pl ? String(pl) : "";

      // Totals
      JsonObject totals = doc["totals"];
      if (totals) {
        total_tokens   = totals["tokens"]     | 0L;
        total_messages = totals["messages"]    | 0;
        total_projects = totals["projects"]    | 0;
        total_sessions = totals["sessions"]    | 0;
        cache_hit_rate = totals["cache_hit_rate"] | 0.0f;
      }

      // Projects (top N by tokens)
      project_count = 0;
      JsonArray projs = doc["projects"];
      if (projs) {
        for (JsonObject pj : projs) {
          if (project_count >= 8) break;
          const char* nm = pj["name"];
          project_names[project_count]  = nm ? String(nm) : "?";
          project_tokens[project_count] = pj["tokens"] | 0L;
          project_count++;
        }
      }

      // Error from server
      const char* srv_err = doc["error"];
      has_error = (srv_err && strlen(srv_err) > 0);
      error_msg = has_error ? String(srv_err).substring(0, 24) : "";

      // ── Buzzer alert: crossed 80% threshold ──
      if (pct_5h >= ALERT_PCT && old_5h < ALERT_PCT && old_5h >= 0 && !alert_fired) {
        soft_chirp();
        alert_fired = true;
      }
      // Reset alert if dropped below threshold (new window)
      if (pct_5h < ALERT_PCT) {
        alert_fired = false;
      }

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

  // Init IMU
  M5.Imu.begin();

  // Init speaker at low volume
  M5.Speaker.begin();
  M5.Speaker.setVolume(40);  // 0-255, keep it gentle

  // Connecting splash
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(CLR_AMBER, CLR_BG);
  M5.Lcd.setCursor(10, 55);
  M5.Lcd.print("clu · trying home...");

  // Try home WiFi first (WPA2-PSK)
  WiFi.begin(HOME_SSID, HOME_PASS);
  for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    M5.Lcd.print(".");
  }

  // Fall back to eduroam (WPA2-Enterprise)
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.disconnect(true);
    delay(100);
    M5.Lcd.fillScreen(CLR_BG);
    M5.Lcd.setCursor(10, 55);
    M5.Lcd.setTextColor(CLR_VIOLET, CLR_BG);
    M5.Lcd.print("clu · trying eduroam...");

    WiFi.mode(WIFI_STA);
    esp_wifi_sta_wpa2_ent_set_identity((uint8_t*)EDU_USER, strlen(EDU_USER));
    esp_wifi_sta_wpa2_ent_set_username((uint8_t*)EDU_USER, strlen(EDU_USER));
    esp_wifi_sta_wpa2_ent_set_password((uint8_t*)EDU_PASS, strlen(EDU_PASS));
    esp_wifi_sta_wpa2_ent_enable();
    WiFi.begin(EDU_SSID);

    for (int i = 0; i < 30 && WiFi.status() != WL_CONNECTED; i++) {
      delay(500);
      M5.Lcd.print(".");
    }
  }

  M5.Lcd.fillScreen(CLR_BG);

  if (WiFi.status() == WL_CONNECTED) {
    fetch_data();
  } else {
    has_error = true;
    error_msg = "WiFi failed";
  }

  draw_current_screen();
  last_fetch_ms = millis();
}

// ─── Loop ─────────────────────────────────────────────────────────────────────
void loop() {
  M5.update();
  tick++;

  // Read accelerometer
  read_imu();

  // ── Tilt-to-sleep: face down (z acceleration negative = screen facing down) ──
  bool face_down = (imu_az < -0.7f);

  if (face_down && !is_asleep) {
    is_asleep = true;
    M5.Lcd.setBrightness(0);
  } else if (!face_down && is_asleep) {
    is_asleep = true;  // will clear on next tick with stable reading
    // Debounce: require 2 consecutive non-face-down readings
    static int wake_count = 0;
    wake_count++;
    if (wake_count > 3) {
      is_asleep = false;
      wake_count = 0;
      M5.Lcd.setBrightness(80);
      draw_current_screen();
    }
  }

  if (is_asleep) {
    delay(100);  // save power when sleeping
    return;
  }

  // ── Button A: cycle screens ──
  if (M5.BtnA.wasPressed()) {
    current_screen = (Screen)(((int)current_screen + 1) % NUM_SCREENS);
    draw_current_screen();
  }

  // ── Button B: short press = refresh, long press (1s) = test beep ──
  if (M5.BtnB.wasReleased()) {
    if (M5.BtnB.lastChange() > 1000) {
      // Long press: test buzzer
      soft_chirp();
    } else {
      // Short press: refresh
      M5.Lcd.setTextColor(CLR_CYAN, CLR_BG);
      M5.Lcd.setTextSize(1);
      M5.Lcd.setCursor(STATS_X, 122);
      M5.Lcd.print("refreshing...");

      fetch_data();
      draw_current_screen();
      last_fetch_ms = millis();
    }
  }

  // Animate mascot every ~250ms
  static unsigned long last_mascot = 0;
  unsigned long now = millis();
  if (now - last_mascot >= 250) {
    draw_mascot(MASCOT_CX, MASCOT_BY, tick);
    last_mascot = now;
  }

  // Refresh stats from server periodically
  if (now - last_fetch_ms >= (unsigned long)REFRESH_MS) {
    fetch_data();
    draw_current_screen();
    last_fetch_ms = now;
  }

  // Redraw countdowns every 10 seconds (only on usage screen)
  static unsigned long last_countdown = 0;
  if (current_screen == SCR_USAGE && now - last_countdown >= 10000) {
    draw_current_screen();
    last_countdown = now;
  }

  delay(50);
}
