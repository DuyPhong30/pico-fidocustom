#!/usr/bin/env python3
"""
patch_tembed.py
Complete LILYGO T-Embed hardware customization for FIDO2 Security Key
1. Power gate GPIO 15 & Backlight GPIO 21
2. Rotary encoder button GPIO 0 with strict User Presence (waits for press)
3. 8-LED WS2812 ring on GPIO 14: OFF in idle, SPINS when waiting, GREEN on confirmed
4. ST7789 170x320 portrait display: Ginger Cat pixel art UI (Idle, Waiting, Confirmed, Timeout)
"""

import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
SDK_DIR = os.path.join(ROOT, "pico-keys-sdk")

TEMBED_DISPLAY_H = """/*
 * LILYGO T-Embed Display Driver for FIDO2 Security Key
 */

#ifndef _TEMBED_DISPLAY_H_
#define _TEMBED_DISPLAY_H_

#include <stdint.h>

typedef enum {
    TEMBED_UI_BOOT = 0,
    TEMBED_UI_READY,
    TEMBED_UI_WAIT_USER,
    TEMBED_UI_SUCCESS,
    TEMBED_UI_ERROR
} tembed_ui_state_t;

void tembed_display_start(void);
void tembed_display_set_state(tembed_ui_state_t state);

#endif // _TEMBED_DISPLAY_H_
"""

TEMBED_DISPLAY_C = """#include "tembed_display.h"
#include "tembed_images.h"
#include "kamen_rider_eyes.h"
#include <string.h>
#include <stdio.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "signal.h"

#define TFT_MOSI 9
#define TFT_SCLK 11
#define TFT_CS   41
#define TFT_DC   16
#define TFT_BL   21
#define TFT_PWR  15

#define SCREEN_W 170
#define SCREEN_H 320
#define OFFSET_X 35
#define OFFSET_Y 0

#define HUD_Y_START 276
#define HUD_H       44
#define HUD_W       170

static spi_device_handle_t spi_lcd = NULL;
static QueueHandle_t ui_queue = NULL;
static volatile uint32_t g_ui_timeout_sec = 30;
static volatile uint32_t g_wait_start_tick = 0;
static int s_last_beam_y = -1;

static uint8_t hud_buf[HUD_W * HUD_H * 2];
static uint8_t eye_buf[64 * 50 * 2];

static void lcd_cmd(uint8_t cmd) {
    gpio_set_level(TFT_DC, 0);
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = 8;
    t.tx_buffer = &cmd;
    spi_device_polling_transmit(spi_lcd, &t);
}

static void lcd_data(const uint8_t *data, int len) {
    if (len <= 0) return;
    gpio_set_level(TFT_DC, 1);
    spi_transaction_t t;
    memset(&t, 0, sizeof(t));
    t.length = len * 8;
    t.tx_buffer = data;
    spi_device_polling_transmit(spi_lcd, &t);
}

static void lcd_data_byte(uint8_t d) {
    lcd_data(&d, 1);
}

static void st7789_set_window(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
    uint16_t x_start = x0 + OFFSET_X;
    uint16_t x_end   = x1 + OFFSET_X;
    uint16_t y_start = y0 + OFFSET_Y;
    uint16_t y_end   = y1 + OFFSET_Y;

    lcd_cmd(0x2A);
    uint8_t data_x[4] = { x_start >> 8, x_start & 0xFF, x_end >> 8, x_end & 0xFF };
    lcd_data(data_x, 4);

    lcd_cmd(0x2B);
    uint8_t data_y[4] = { y_start >> 8, y_start & 0xFF, y_end >> 8, y_end & 0xFF };
    lcd_data(data_y, 4);

    lcd_cmd(0x2C);
}

static void st7789_draw_rle(const uint8_t *rle_data, size_t rle_len) {
    st7789_set_window(0, 0, SCREEN_W - 1, SCREEN_H - 1);
    static uint8_t chunk[512];
    size_t chunk_idx = 0;
    size_t i = 0;
    while (i < rle_len) {
        uint8_t count = rle_data[i++];
        uint8_t hi = rle_data[i++];
        uint8_t lo = rle_data[i++];
        for (int c = 0; c < count; c++) {
            chunk[chunk_idx++] = hi;
            chunk[chunk_idx++] = lo;
            if (chunk_idx >= sizeof(chunk)) {
                lcd_data(chunk, chunk_idx);
                chunk_idx = 0;
            }
        }
    }
    if (chunk_idx > 0) {
        lcd_data(chunk, chunk_idx);
    }
}

static void st7789_hw_init(void) {
    gpio_reset_pin(TFT_DC);
    gpio_set_direction(TFT_DC, GPIO_MODE_OUTPUT);
    gpio_set_level(TFT_DC, 1);

    spi_bus_config_t buscfg = {
        .mosi_io_num = TFT_MOSI,
        .miso_io_num = -1,
        .sclk_io_num = TFT_SCLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 1024,
    };
    spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);

    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = 40 * 1000 * 1000,
        .mode = 0,
        .spics_io_num = TFT_CS,
        .queue_size = 7,
    };
    spi_bus_add_device(SPI2_HOST, &devcfg, &spi_lcd);

    lcd_cmd(0x01);
    vTaskDelay(pdMS_TO_TICKS(150));

    lcd_cmd(0x11);
    vTaskDelay(pdMS_TO_TICKS(120));

    lcd_cmd(0x3A);
    lcd_data_byte(0x55);

    lcd_cmd(0x36); // Portrait mode (colstart=35, rowstart=0)
    lcd_data_byte(0x00);

    lcd_cmd(0x21);
    lcd_cmd(0x13);
    vTaskDelay(pdMS_TO_TICKS(10));

    lcd_cmd(0x29);
    vTaskDelay(pdMS_TO_TICKS(50));
}

static const uint8_t font5x7_digits[][5] = {
    { 0x3E, 0x51, 0x49, 0x45, 0x3E }, // 0
    { 0x00, 0x42, 0x7F, 0x40, 0x00 }, // 1
    { 0x42, 0x61, 0x51, 0x49, 0x46 }, // 2
    { 0x21, 0x41, 0x45, 0x4B, 0x31 }, // 3
    { 0x18, 0x14, 0x12, 0x7F, 0x10 }, // 4
    { 0x27, 0x45, 0x45, 0x45, 0x39 }, // 5
    { 0x3C, 0x4A, 0x49, 0x49, 0x30 }, // 6
    { 0x01, 0x71, 0x09, 0x05, 0x03 }, // 7
    { 0x36, 0x49, 0x49, 0x49, 0x36 }, // 8
    { 0x06, 0x49, 0x49, 0x29, 0x1E }, // 9
};

static const uint8_t *get_glyph(char c) {
    static const uint8_t g_space[5] = {0,0,0,0,0};
    static const uint8_t g_brack_l[5] = {0x00, 0x7F, 0x41, 0x41, 0x00}; // '['
    static const uint8_t g_brack_r[5] = {0x00, 0x41, 0x41, 0x7F, 0x00}; // ']'
    static const uint8_t g_s[5] = {0x48, 0x54, 0x54, 0x54, 0x20};       // 's'
    static const uint8_t g_p[5] = {0x7F, 0x09, 0x09, 0x09, 0x06};       // 'P'
    static const uint8_t g_r[5] = {0x7F, 0x09, 0x19, 0x29, 0x46};       // 'R'
    static const uint8_t g_e[5] = {0x7F, 0x49, 0x49, 0x49, 0x41};       // 'E'
    static const uint8_t g_k[5] = {0x7F, 0x08, 0x14, 0x22, 0x41};       // 'K'
    static const uint8_t g_n[5] = {0x7F, 0x04, 0x08, 0x10, 0x7F};       // 'N'
    static const uint8_t g_o[5] = {0x3E, 0x41, 0x41, 0x41, 0x3E};       // 'O'
    static const uint8_t g_b[5] = {0x7F, 0x49, 0x49, 0x49, 0x36};       // 'B'
    static const uint8_t g_gt[5] = {0x41, 0x22, 0x14, 0x08, 0x00};      // '>'
    static const uint8_t g_lt[5] = {0x08, 0x14, 0x22, 0x41, 0x00};      // '<'
    static const uint8_t g_excl[5] = {0x00, 0x00, 0x5F, 0x00, 0x00};    // '!'
    static const uint8_t g_colon[5] = {0x00, 0x36, 0x36, 0x00, 0x00};   // ':'

    if (c >= '0' && c <= '9') return font5x7_digits[c - '0'];
    if (c == '[') return g_brack_l;
    if (c == ']') return g_brack_r;
    if (c == 's' || c == 'S') return g_s;
    if (c == 'P') return g_p;
    if (c == 'R') return g_r;
    if (c == 'E') return g_e;
    if (c == 'K') return g_k;
    if (c == 'N') return g_n;
    if (c == 'O') return g_o;
    if (c == 'B') return g_b;
    if (c == '>') return g_gt;
    if (c == '<') return g_lt;
    if (c == '!') return g_excl;
    if (c == ':') return g_colon;
    return g_space;
}

static void hud_clear(uint16_t bg_color) {
    uint8_t hi = (bg_color >> 8) & 0xFF;
    uint8_t lo = bg_color & 0xFF;
    for (int i = 0; i < HUD_W * HUD_H; i++) {
        hud_buf[i * 2]     = hi;
        hud_buf[i * 2 + 1] = lo;
    }
}

static inline void hud_set_pixel(int x, int y, uint16_t color) {
    int ly = y - HUD_Y_START;
    if (x < 0 || x >= HUD_W || ly < 0 || ly >= HUD_H) return;
    int idx = (ly * HUD_W + x) * 2;
    hud_buf[idx]     = (color >> 8) & 0xFF;
    hud_buf[idx + 1] = color & 0xFF;
}

static void hud_draw_rect(int x, int y, int w, int h, uint16_t color) {
    for (int px = x; px < x + w; px++) {
        hud_set_pixel(px, y, color);
        hud_set_pixel(px, y + h - 1, color);
    }
    for (int py = y; py < y + h; py++) {
        hud_set_pixel(x, py, color);
        hud_set_pixel(x + w - 1, py, color);
    }
}

static void hud_fill_rect(int x, int y, int w, int h, uint16_t color) {
    for (int py = y; py < y + h; py++) {
        for (int px = x; px < x + w; px++) {
            hud_set_pixel(px, py, color);
        }
    }
}

static void hud_draw_char(int x, int y, char c, uint16_t color, int scale) {
    const uint8_t *glyph = get_glyph(c);
    for (int col = 0; col < 5; col++) {
        uint8_t line = glyph[col];
        for (int row = 0; row < 7; row++) {
            if (line & (1 << row)) {
                if (scale == 1) {
                    hud_set_pixel(x + col, y + row, color);
                } else {
                    for (int dy = 0; dy < scale; dy++) {
                        for (int dx = 0; dx < scale; dx++) {
                            hud_set_pixel(x + col * scale + dx, y + row * scale + dy, color);
                        }
                    }
                }
            }
        }
    }
}

static void hud_draw_string(int x, int y, const char *str, uint16_t color, int scale) {
    int cur_x = x;
    while (*str) {
        hud_draw_char(cur_x, y, *str, color, scale);
        cur_x += (5 * scale) + (scale == 1 ? 1 : 2);
        str++;
    }
}

static void tembed_render_hud(float progress, int remaining_sec, uint32_t frame) {
    // 1. Dark futuristic slate background
    hud_clear(0x0842); // very deep sleek navy / cyber space

    // 2. High-tech border and corner accents
    hud_draw_rect(2, HUD_Y_START + 1, HUD_W - 4, HUD_H - 2, 0x03EF);
    hud_fill_rect(2, HUD_Y_START + 1, 4, 2, 0x07FF);
    hud_fill_rect(HUD_W - 6, HUD_Y_START + 1, 4, 2, 0x07FF);
    hud_fill_rect(2, HUD_Y_START + HUD_H - 3, 4, 2, 0x07FF);
    hud_fill_rect(HUD_W - 6, HUD_Y_START + HUD_H - 3, 4, 2, 0x07FF);

    // 3. Countdown Energy Bar (Y = 280 to 286, X = 15 to 154, width = 140)
    int bar_x = 15;
    int bar_y = HUD_Y_START + 4;
    int bar_w = 140;
    int bar_h = 7;
    hud_draw_rect(bar_x, bar_y, bar_w, bar_h, 0x7BEF);

    uint16_t energy_color;
    if (remaining_sec > 12) {
        energy_color = 0x07E0; // Neon Emerald Green (Gaia Memory active)
    } else if (remaining_sec > 5) {
        energy_color = 0xFD20; // Amber Orange (Warning)
    } else {
        energy_color = ((frame / 4) % 2 == 0) ? 0xF800 : 0x8000; // Critical Flashing Red
    }

    int fill_w = (int)((bar_w - 2) * progress);
    if (fill_w < 0) fill_w = 0;
    if (fill_w > bar_w - 2) fill_w = bar_w - 2;

    for (int px = 0; px < fill_w; px++) {
        if ((px % 4) == 3) continue; // Retro sci-fi segment gap
        for (int py = bar_y + 1; py < bar_y + bar_h - 1; py++) {
            hud_set_pixel(bar_x + 1 + px, py, energy_color);
        }
    }

    // 4. Large Digital Countdown Clock: "[ 28s ]" (scale = 2)
    char time_str[8];
    int sec = (remaining_sec < 0) ? 0 : ((remaining_sec > 99) ? 99 : remaining_sec);
    time_str[0] = '[';
    time_str[1] = ' ';
    time_str[2] = (sec >= 10) ? ('0' + (sec / 10)) : ' ';
    time_str[3] = '0' + (sec % 10);
    time_str[4] = 's';
    time_str[5] = ' ';
    time_str[6] = ']';
    time_str[7] = '\\0';
    int time_x = (HUD_W - (7 * 12)) / 2; // Centered at X=43
    uint16_t text_color = (remaining_sec <= 5) ? energy_color : 0xFFFF;
    hud_draw_string(time_x, HUD_Y_START + 14, time_str, text_color, 2);

    // 5. Blinking Instruction Prompt: ">> PRESS KNOB <<" (scale = 1)
    if ((frame / 10) % 2 == 0) {
        const char *prompt = ">> PRESS KNOB <<";
        int prompt_x = (HUD_W - (16 * 6)) / 2;
        hud_draw_string(prompt_x, HUD_Y_START + 31, prompt, 0xFFE0, 1); // Henshin Gold
    }

    // Single burst write to ST7789
    st7789_set_window(0, HUD_Y_START, HUD_W - 1, HUD_Y_START + HUD_H - 1);
    lcd_data(hud_buf, sizeof(hud_buf));
}

static void tembed_render_eyes(uint32_t frame) {
    float phase = (float)frame * 0.15f;
    float pulse = 0.60f + 0.40f * sinf(phase);

    int glint_col = (frame % 70) * 2 - 15; // Anime visor flare glint every ~3.5s

    for (int y = 0; y < W_EYE_H; y++) {
        for (int x = 0; x < W_EYE_W; x++) {
            int idx = y * W_EYE_W + x;
            uint16_t c = w_eye_base[idx];
            uint8_t r = (c >> 11) & 0x1F;
            uint8_t g = (c >> 5) & 0x3F;
            uint8_t b = c & 0x1F;

            if (r > 12 && g < 15 && b < 15) { // Ruby compound eye pixel
                uint8_t r_new = (uint8_t)(r * pulse);
                if (r_new > 31) r_new = 31;
                if (r_new < 5) r_new = 5;

                if (pulse > 0.85f && r > 20) {
                    g += (uint8_t)((pulse - 0.85f) * 50.0f);
                    if (g > 63) g = 63;
                }

                int dist = (x - y / 2) - glint_col;
                if (dist >= -1 && dist <= 1) {
                    r_new = 31;
                    g = 63;
                    b = 31; // Brilliant lens flare glint
                }

                uint16_t pix = (r_new << 11) | (g << 5) | b;
                eye_buf[idx * 2]     = (pix >> 8) & 0xFF;
                eye_buf[idx * 2 + 1] = pix & 0xFF;
            } else {
                eye_buf[idx * 2]     = (c >> 8) & 0xFF;
                eye_buf[idx * 2 + 1] = c & 0xFF;
            }
        }
    }

    st7789_set_window(W_EYE_X, W_EYE_Y, W_EYE_X + W_EYE_W - 1, W_EYE_Y + W_EYE_H - 1);
    lcd_data(eye_buf, W_EYE_W * W_EYE_H * 2);
}

static void tembed_render_fourze_idle(uint32_t frame) {
    float phase = (float)frame * 0.12f;
    float pulse = 0.70f + 0.30f * sinf(phase);

    // 1. Fourze Orange Breathing Visor Eyes
    for (int y = 0; y < FOURZE_EYE_H; y++) {
        for (int x = 0; x < FOURZE_EYE_W; x++) {
            int idx = y * FOURZE_EYE_W + x;
            uint16_t c = fourze_eye_base[idx];
            uint8_t r = (c >> 11) & 0x1F;
            uint8_t g = (c >> 5) & 0x3F;
            uint8_t b = c & 0x1F;

            // Orange compound eye pixels: high red, mid green, low blue
            if (r > 18 && g > 8 && g < 36 && b < 10) {
                uint8_t r_new = (uint8_t)(r * pulse);
                if (r_new > 31) r_new = 31;
                if (r_new < 8) r_new = 8;

                uint8_t g_new = (uint8_t)(g * pulse);
                if (g_new > 45) g_new = 45;

                // Center core flare when breathing reaches peak
                if (pulse > 0.88f && r > 25 && g > 15) {
                    r_new = 31;
                    g_new = 55; // White-hot amber glow
                }

                uint16_t pix = (r_new << 11) | (g_new << 5) | b;
                eye_buf[idx * 2]     = (pix >> 8) & 0xFF;
                eye_buf[idx * 2 + 1] = pix & 0xFF;
            } else {
                eye_buf[idx * 2]     = (c >> 8) & 0xFF;
                eye_buf[idx * 2 + 1] = c & 0xFF;
            }
        }
    }
    st7789_set_window(FOURZE_EYE_X, FOURZE_EYE_Y, FOURZE_EYE_X + FOURZE_EYE_W - 1, FOURZE_EYE_Y + FOURZE_EYE_H - 1);
    lcd_data(eye_buf, FOURZE_EYE_W * FOURZE_EYE_H * 2);

    // 2. Rocket thruster flicker at exhaust (X=83..86, Y=244..247)
    static const uint16_t flame_colors[4] = { 0xFD20, 0xFFE0, 0xFA00, 0xFFFF };
    uint8_t flame_buf[4 * 4 * 2];
    uint16_t flame_col = flame_colors[(frame + (frame / 2)) % 4];
    uint8_t f_hi = (flame_col >> 8) & 0xFF;
    uint8_t f_lo = flame_col & 0xFF;
    for (int i = 0; i < 4 * 4; i++) {
        flame_buf[i * 2]     = f_hi;
        flame_buf[i * 2 + 1] = f_lo;
    }
    st7789_set_window(83, 244, 86, 247);
    lcd_data(flame_buf, sizeof(flame_buf));

    // 3. Cosmic starlight particles in left & right space margins
    int star1_y = 40 + ((frame * 2) % 200);
    int star2_y = 200 - ((frame * 3) % 180);
    static const uint8_t star_pix[2 * 2 * 2] = { 0x07, 0xFF, 0x07, 0xFF, 0x07, 0xFF, 0x07, 0xFF }; // Cyan cyber star
    st7789_set_window(4, star1_y, 5, star1_y + 1);
    lcd_data(star_pix, sizeof(star_pix));
    st7789_set_window(164, star2_y, 165, star2_y + 1);
    lcd_data(star_pix, sizeof(star_pix));
}

static void tembed_render_faiz_complete(uint32_t frame) {
    if (frame < 4) {
        // Blinding golden-white visor flash impact (first ~150ms)!
        for (int i = 0; i < FAIZ_EYE_W * FAIZ_EYE_H; i++) {
            uint16_t c = faiz_eye_base[i];
            uint8_t r = (c >> 11) & 0x1F;
            uint8_t g = (c >> 5) & 0x3F;
            uint8_t b = c & 0x1F;
            if (r > 15 && g > 15) {
                eye_buf[i * 2]     = 0xFF;
                eye_buf[i * 2 + 1] = 0xE0; // Henshin Gold
            } else if (r > 15 && g < 10) {
                eye_buf[i * 2]     = 0xF8;
                eye_buf[i * 2 + 1] = 0x00; // Neon scarlet
            } else {
                eye_buf[i * 2]     = (c >> 8) & 0xFF;
                eye_buf[i * 2 + 1] = c & 0xFF;
            }
        }
    } else {
        // Blazing yellow Ultimate Finder eyes pulsing & red photon streams pumping
        float pulse = 0.75f + 0.25f * sinf((float)(frame - 4) * 0.28f);

        for (int i = 0; i < FAIZ_EYE_W * FAIZ_EYE_H; i++) {
            uint16_t c = faiz_eye_base[i];
            uint8_t r = (c >> 11) & 0x1F;
            uint8_t g = (c >> 5) & 0x3F;
            uint8_t b = c & 0x1F;

            // Ultimate Finder circular eyes: bright yellow/amber
            if (r > 20 && g > 25 && b < 20) {
                uint8_t r_new = (uint8_t)(r * pulse);
                uint8_t g_new = (uint8_t)(g * pulse);
                if (r_new > 31) r_new = 31;
                if (g_new > 63) g_new = 63;

                // Center core white-hot glare
                if (pulse > 0.85f && r > 28 && g > 50) {
                    r_new = 31;
                    g_new = 63;
                    b = 20; // Blazing white-gold center
                }
                uint16_t pix = (r_new << 11) | (g_new << 5) | b;
                eye_buf[i * 2]     = (pix >> 8) & 0xFF;
                eye_buf[i * 2 + 1] = pix & 0xFF;
            } else if (r > 16 && g < 12 && b < 12) {
                // Red Photon Blood Stream outlines: pulsating crimson
                uint8_t r_crimson = (uint8_t)(31.0f * (0.80f + 0.20f * pulse));
                if (r_crimson > 31) r_crimson = 31;
                uint16_t pix = (r_crimson << 11) | (g << 5) | b;
                eye_buf[i * 2]     = (pix >> 8) & 0xFF;
                eye_buf[i * 2 + 1] = pix & 0xFF;
            } else {
                eye_buf[i * 2]     = (c >> 8) & 0xFF;
                eye_buf[i * 2 + 1] = c & 0xFF;
            }
        }
    }
    st7789_set_window(FAIZ_EYE_X, FAIZ_EYE_Y, FAIZ_EYE_X + FAIZ_EYE_W - 1, FAIZ_EYE_Y + FAIZ_EYE_H - 1);
    lcd_data(eye_buf, FAIZ_EYE_W * FAIZ_EYE_H * 2);

    // Victory sparkles around padlock (alternating stars)
    uint16_t spark_col = ((frame / 3) % 2 == 0) ? 0xFFE0 : 0xFFFF;
    uint8_t spk[2 * 2 * 2];
    uint8_t shi = (spark_col >> 8) & 0xFF;
    uint8_t slo = spark_col & 0xFF;
    for (int i = 0; i < 4; i++) { spk[i * 2] = shi; spk[i * 2 + 1] = slo; }
    st7789_set_window(45, 222, 46, 223);
    lcd_data(spk, sizeof(spk));
    st7789_set_window(123, 222, 124, 223);
    lcd_data(spk, sizeof(spk));
}

static void tembed_render_space(uint32_t frame) {
    int beam_y = 15 + ((frame * 3) % 245);

    if (s_last_beam_y >= 0 && s_last_beam_y != beam_y) {
        static const uint8_t bg_clean[5 * 3 * 2] = {
            0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E,
            0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E,
            0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E, 0xCE, 0x7E
        };
        st7789_set_window(3, s_last_beam_y, 7, s_last_beam_y + 2);
        lcd_data(bg_clean, sizeof(bg_clean));

        st7789_set_window(162, s_last_beam_y, 166, s_last_beam_y + 2);
        lcd_data(bg_clean, sizeof(bg_clean));
    }

    uint16_t beam_col = ((frame / 15) % 2 == 0) ? 0x07FF : 0x07E0;
    uint8_t beam_pixels[5 * 3 * 2];
    uint8_t hi = (beam_col >> 8) & 0xFF;
    uint8_t lo = beam_col & 0xFF;
    for (int i = 0; i < 5 * 3; i++) {
        beam_pixels[i * 2]     = hi;
        beam_pixels[i * 2 + 1] = lo;
    }

    st7789_set_window(3, beam_y, 7, beam_y + 2);
    lcd_data(beam_pixels, sizeof(beam_pixels));

    st7789_set_window(162, beam_y, 166, beam_y + 2);
    lcd_data(beam_pixels, sizeof(beam_pixels));

    s_last_beam_y = beam_y;
}

static void tembed_draw_screen(tembed_ui_state_t state) {
    switch (state) {
        case TEMBED_UI_READY:
            st7789_draw_rle(img_idle_rle, img_idle_len);
            break;
        case TEMBED_UI_WAIT_USER:
            st7789_draw_rle(img_waiting_rle, img_waiting_len);
            break;
        case TEMBED_UI_SUCCESS:
            st7789_draw_rle(img_confirmed_rle, img_confirmed_len);
            break;
        case TEMBED_UI_ERROR:
            st7789_draw_rle(img_timeout_rle, img_timeout_len);
            break;
        default:
            break;
    }
}

static void tembed_ui_task(void *pvParameters) {
    (void)pvParameters;
    st7789_hw_init();
    tembed_draw_screen(TEMBED_UI_READY);

    tembed_ui_state_t current_state = TEMBED_UI_READY;
    tembed_ui_state_t new_state;
    uint32_t anim_frame = 0;
    uint32_t success_start_tick = 0;

    while (1) {
        TickType_t wait_ticks;
        if (current_state == TEMBED_UI_WAIT_USER) {
            wait_ticks = pdMS_TO_TICKS(50); // 20 FPS live animation loop (W Countdown)
        } else if (current_state == TEMBED_UI_READY) {
            wait_ticks = pdMS_TO_TICKS(80); // ~12 FPS live animation loop (Fourze Idle)
        } else if (current_state == TEMBED_UI_SUCCESS) {
            wait_ticks = pdMS_TO_TICKS(50); // 20 FPS live animation loop (Faiz Complete)
        } else if (current_state == TEMBED_UI_ERROR) {
            wait_ticks = pdMS_TO_TICKS(2500); // 2.5s display for Timeout card
        } else {
            wait_ticks = portMAX_DELAY;
        }

        if (xQueueReceive(ui_queue, &new_state, wait_ticks) == pdTRUE) {
            if (new_state != current_state) {
                current_state = new_state;
                s_last_beam_y = -1;
                tembed_draw_screen(current_state);

                if (current_state == TEMBED_UI_WAIT_USER) {
                    g_wait_start_tick = xTaskGetTickCount();
                    anim_frame = 0;
                } else if (current_state == TEMBED_UI_SUCCESS) {
                    success_start_tick = xTaskGetTickCount();
                    anim_frame = 0;
                } else if (current_state == TEMBED_UI_READY) {
                    anim_frame = 0;
                }
            }
        } else {
            if (current_state == TEMBED_UI_READY) {
                anim_frame++;
                tembed_render_fourze_idle(anim_frame);

            } else if (current_state == TEMBED_UI_WAIT_USER) {
                anim_frame++;
                uint32_t now_tick = xTaskGetTickCount();
                uint32_t elapsed_ms = (now_tick >= g_wait_start_tick) ? 
                    ((now_tick - g_wait_start_tick) * 1000 / configTICK_RATE_HZ) : 0;
                uint32_t total_ms = (g_ui_timeout_sec > 0) ? (g_ui_timeout_sec * 1000) : 30000;

                int remaining_sec = (total_ms > elapsed_ms) ? (int)((total_ms - elapsed_ms) / 1000) : 0;
                float progress = (float)(total_ms - elapsed_ms) / (float)total_ms;
                if (progress < 0.0f) progress = 0.0f;
                if (progress > 1.0f) progress = 1.0f;

                tembed_render_eyes(anim_frame);
                tembed_render_space(anim_frame);
                tembed_render_hud(progress, remaining_sec, anim_frame);

            } else if (current_state == TEMBED_UI_SUCCESS) {
                anim_frame++;
                uint32_t now_tick = xTaskGetTickCount();
                uint32_t elapsed_ms = (now_tick >= success_start_tick) ? 
                    ((now_tick - success_start_tick) * 1000 / configTICK_RATE_HZ) : 0;

                if (elapsed_ms < 3200) {
                    tembed_render_faiz_complete(anim_frame);
                } else {
                    current_state = TEMBED_UI_READY;
                    tembed_draw_screen(TEMBED_UI_READY);
                }

            } else if (current_state == TEMBED_UI_ERROR) {
                current_state = TEMBED_UI_READY;
                tembed_draw_screen(TEMBED_UI_READY);
            }
        }
    }
}

void tembed_display_set_state(tembed_ui_state_t state) {
    if (ui_queue) {
        xQueueSend(ui_queue, &state, 0);
    }
}

static int tembed_signal_cb(signal_code_t code, void *data) {
    switch (code) {
        case SIGNAL_BOOT:
        case SIGNAL_USB_MOUNTED:
            tembed_display_set_state(TEMBED_UI_READY);
            break;
        case SIGNAL_USER_PRESENCE_REQUEST:
            if (data) {
                signal_user_presence_request_data_t *req = (signal_user_presence_request_data_t *)data;
                g_ui_timeout_sec = (req->timeout > 0) ? req->timeout : 30;
            } else {
                g_ui_timeout_sec = 30;
            }
            tembed_display_set_state(TEMBED_UI_WAIT_USER);
            break;
        case SIGNAL_USER_PRESENCE_COMPLETED:
            tembed_display_set_state(TEMBED_UI_SUCCESS);
            break;
        case SIGNAL_USER_PRESENCE_TIMEOUT:
            tembed_display_set_state(TEMBED_UI_ERROR);
            break;
        case SIGNAL_USER_PRESENCE_CANCELLED:
            tembed_display_set_state(TEMBED_UI_READY);
            break;
        default:
            break;
    }
    return 0;
}

void tembed_display_start(void) {
    ui_queue = xQueueCreate(8, sizeof(tembed_ui_state_t));
    xTaskCreate(tembed_ui_task, "tembed_ui", 4096, NULL, 1, NULL);

    signal_add(SIGNAL_BOOT, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USB_MOUNTED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_REQUEST, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_COMPLETED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_CANCELLED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_TIMEOUT, SIGNAL_FLAG_NONE, tembed_signal_cb);
}
"""

TEMBED_AUDIO_H = """/*
 * LILYGO T-Embed CC1101 Plus Audio Driver for FIDO2 Security Key
 * Chip: MAX98357A I2S Class-D DAC/Amp
 * Pins: BCLK = GPIO 46, LRCLK/WS = GPIO 40, DOUT = GPIO 7, PWR_EN = GPIO 15
 */

#ifndef _TEMBED_AUDIO_H_
#define _TEMBED_AUDIO_H_

#include <stdint.h>

typedef enum {
    TEMBED_SOUND_NONE = 0,
    TEMBED_SOUND_BOOT,      // Fourze "3, 2, 1!" countdown startup chime
    TEMBED_SOUND_WAITING,   // Faiz "Standing By" long continuous sound loop
    TEMBED_SOUND_CONFIRMED, // Faiz "COMPLETE!" speech
    TEMBED_SOUND_TIMEOUT,   // Buzzer Error
    TEMBED_SOUND_STOP
} tembed_sound_t;

void tembed_audio_start(void);
void tembed_audio_play(tembed_sound_t sound);

#endif // _TEMBED_AUDIO_H_
"""

TEMBED_AUDIO_C = """#include "tembed_audio.h"
#include "tembed_audio_data.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "esp_idf_version.h"
#include "signal.h"

// Official LILYGO T-Embed CC1101 Plus Audio Pinout
#define I2S_BCLK_PIN GPIO_NUM_46
#define I2S_WS_PIN   GPIO_NUM_40
#define I2S_DOUT_PIN GPIO_NUM_7

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
#include "driver/i2s_std.h"
static i2s_chan_handle_t tx_chan = NULL;
#else
#include "driver/i2s.h"
#endif

static QueueHandle_t audio_queue = NULL;

static void tembed_i2s_init(void) {
    // 1. Enable LilyGO T-Embed CC1101 Plus Board Power Gate (GPIO 15)
    gpio_set_direction(GPIO_NUM_15, GPIO_MODE_OUTPUT);
    gpio_set_level(GPIO_NUM_15, 1);

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;
    i2s_new_channel(&chan_cfg, &tx_chan, NULL);

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_BCLK_PIN,
            .ws   = I2S_WS_PIN,
            .dout = I2S_DOUT_PIN,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv   = false,
            },
        },
    };
    i2s_channel_init_std_mode(tx_chan, &std_cfg);
    i2s_channel_enable(tx_chan);
#else
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 6,
        .dma_buf_len = 256,
        .use_apll = false,
        .tx_desc_auto_clear = true
    };
    i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_BCLK_PIN,
        .ws_io_num = I2S_WS_PIN,
        .data_out_num = I2S_DOUT_PIN,
        .data_in_num = I2S_PIN_NO_CHANGE
    };
    i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
    i2s_set_pin(I2S_NUM_0, &pin_config);
    i2s_zero_dma_buffer(I2S_NUM_0);
#endif
}

static void tembed_i2s_write(const void *src, size_t size) {
    size_t written = 0;
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
    if (tx_chan) {
        i2s_channel_write(tx_chan, src, size, &written, portMAX_DELAY);
    }
#else
    i2s_write(I2S_NUM_0, src, size, &written, portMAX_DELAY);
#endif
}

static void tembed_i2s_silence(size_t samples) {
    static int16_t zero_buf[128 * 2] = {0};
    size_t remaining = samples;
    while (remaining > 0) {
        size_t chunk = remaining > 128 ? 128 : remaining;
        tembed_i2s_write(zero_buf, chunk * 2 * sizeof(int16_t));
        remaining -= chunk;
    }
}

static volatile tembed_sound_t s_active_sound = TEMBED_SOUND_NONE;

static void tembed_audio_task(void *pvParameters) {
    (void)pvParameters;
    tembed_i2s_init();

    tembed_sound_t cur_sound = TEMBED_SOUND_NONE;
    int16_t stereo_buf[128 * 2];

    while (1) {
        if (cur_sound == TEMBED_SOUND_NONE || cur_sound == TEMBED_SOUND_STOP) {
            if (xQueueReceive(audio_queue, &cur_sound, portMAX_DELAY) != pdTRUE) {
                continue;
            }
        }

        if (cur_sound == TEMBED_SOUND_STOP || cur_sound == TEMBED_SOUND_NONE) {
            tembed_i2s_silence(128);
            cur_sound = TEMBED_SOUND_NONE;
            continue;
        }

        if (cur_sound == TEMBED_SOUND_CONFIRMED) {
            s_active_sound = TEMBED_SOUND_CONFIRMED;
        }

        const uint8_t *pcm = NULL;
        size_t total_samples = 0;
        bool loop = false;

        switch (cur_sound) {
            case TEMBED_SOUND_BOOT:
                pcm = sound_boot_pcm;
                total_samples = sound_boot_samples;
                loop = false;
                break;
            case TEMBED_SOUND_WAITING:
                pcm = sound_waiting_pcm;
                total_samples = sound_waiting_samples;
                loop = true;
                break;
            case TEMBED_SOUND_CONFIRMED:
                pcm = sound_confirmed_pcm;
                total_samples = sound_confirmed_samples;
                loop = false;
                break;
            case TEMBED_SOUND_TIMEOUT:
                pcm = sound_timeout_pcm;
                total_samples = sound_timeout_samples;
                loop = false;
                break;
            default:
                break;
        }

        if (!pcm || total_samples == 0) {
            cur_sound = TEMBED_SOUND_NONE;
            continue;
        }

        size_t pos = 0;
        bool switched = false;

        while (pos < total_samples) {
            tembed_sound_t req;
            if (cur_sound != TEMBED_SOUND_CONFIRMED && xQueueReceive(audio_queue, &req, 0) == pdTRUE) {
                if (cur_sound == TEMBED_SOUND_BOOT && req == TEMBED_SOUND_STOP) {
                    // Ignore stop for boot chime so it plays to completion
                } else {
                    cur_sound = req;
                    switched = true;
                    break; // Switch to new sound immediately!
                }
            }

            size_t chunk = (total_samples - pos > 128) ? 128 : (total_samples - pos);
            for (size_t i = 0; i < chunk; i++) {
                int32_t raw_val = (int32_t)pcm[pos + i] - 128; // -128 .. +127
                int16_t s16 = (int16_t)((raw_val * 256 * 35) / 100); // 35% volume as requested
                stereo_buf[i * 2]     = s16;
                stereo_buf[i * 2 + 1] = s16;
            }
            tembed_i2s_write(stereo_buf, chunk * 2 * sizeof(int16_t));
            pos += chunk;
        }

        if (switched) {
            // cur_sound contains the new sound (e.g. CONFIRMED or STOP)
            continue;
        }

        if (loop) {
            // Check queue frequently during short inter-loop gap
            for (int p = 0; p < 8; p++) {
                tembed_sound_t req;
                if (xQueueReceive(audio_queue, &req, pdMS_TO_TICKS(50)) == pdTRUE) {
                    cur_sound = req;
                    switched = true;
                    break;
                }
            }
            if (switched) {
                continue;
            }
            // Loop again
            continue;
        } else {
            // One-shot sound finished (BOOT, CONFIRMED, or TIMEOUT)
            tembed_i2s_silence(256);
            if (cur_sound == TEMBED_SOUND_CONFIRMED) {
                s_active_sound = TEMBED_SOUND_NONE;
            }
            cur_sound = TEMBED_SOUND_NONE;
        }
    }
}

void tembed_audio_play(tembed_sound_t sound) {
    if (!audio_queue) return;

    // COMPLETE sound must NEVER be cancelled by STOP
    if (s_active_sound == TEMBED_SOUND_CONFIRMED && sound == TEMBED_SOUND_STOP) {
        return;
    }

    if (sound == TEMBED_SOUND_CONFIRMED) {
        s_active_sound = TEMBED_SOUND_CONFIRMED;
        xQueueReset(audio_queue);
        xQueueSend(audio_queue, &sound, 0);
        return;
    }

    if (sound == TEMBED_SOUND_STOP) {
        xQueueReset(audio_queue);
        xQueueSend(audio_queue, &sound, 0);
        return;
    }

    xQueueSend(audio_queue, &sound, 0);
}

static int tembed_audio_signal_cb(signal_code_t code, void *data) {
    (void)data;
    switch (code) {
        case SIGNAL_USER_PRESENCE_REQUEST:
            tembed_audio_play(TEMBED_SOUND_WAITING);
            break;
        case SIGNAL_USER_PRESENCE_COMPLETED:
            tembed_audio_play(TEMBED_SOUND_CONFIRMED);
            break;
        case SIGNAL_USER_PRESENCE_TIMEOUT:
            tembed_audio_play(TEMBED_SOUND_TIMEOUT);
            break;
        case SIGNAL_USER_PRESENCE_CANCELLED:
            tembed_audio_play(TEMBED_SOUND_STOP);
            break;
        default:
            break;
    }
    return 0;
}

void tembed_audio_start(void) {
    audio_queue = xQueueCreate(8, sizeof(tembed_sound_t));
    xTaskCreate(tembed_audio_task, "tembed_audio", 4096, NULL, 2, NULL);

    // Play Fourze 3-2-1 boot chime on startup
    tembed_audio_play(TEMBED_SOUND_BOOT);

    signal_add(SIGNAL_USER_PRESENCE_REQUEST, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_COMPLETED, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_TIMEOUT, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_CANCELLED, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
}
"""

def copy_images():
    src = os.path.join(ROOT, "tembed_images.h")
    dst = os.path.join(SDK_DIR, "src", "tembed_images.h")
    if os.path.exists(src):
        shutil.copy(src, dst)
        print("Copied tembed_images.h to SDK")

def copy_audio():
    src = os.path.join(ROOT, "tembed_audio_data.h")
    dst = os.path.join(SDK_DIR, "src", "tembed_audio_data.h")
    if os.path.exists(src):
        shutil.copy(src, dst)
        print("Copied tembed_audio_data.h to SDK")

def copy_eye_data():
    src = os.path.join(ROOT, "kamen_rider_eyes.h")
    dst = os.path.join(SDK_DIR, "src", "kamen_rider_eyes.h")
    if os.path.exists(src):
        shutil.copy(src, dst)
        print("Copied kamen_rider_eyes.h to SDK")

def create_display_files():
    src_dir = os.path.join(SDK_DIR, "src")
    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "tembed_display.h"), "w", encoding="utf-8") as f:
        f.write(TEMBED_DISPLAY_H)
    with open(os.path.join(src_dir, "tembed_display.c"), "w", encoding="utf-8") as f:
        f.write(TEMBED_DISPLAY_C)
    print("Created tembed_display.h and tembed_display.c")

def create_audio_files():
    src_dir = os.path.join(SDK_DIR, "src")
    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "tembed_audio.h"), "w", encoding="utf-8") as f:
        f.write(TEMBED_AUDIO_H)
    with open(os.path.join(src_dir, "tembed_audio.c"), "w", encoding="utf-8") as f:
        f.write(TEMBED_AUDIO_C)
    print("Created tembed_audio.h and tembed_audio.c")

def patch_main_c():
    path = os.path.join(SDK_DIR, "src", "main.c")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if "tembed_audio_start" in content:
        if "zw111_init" not in content:
            content = content.replace(
                "tembed_audio_start();",
                "tembed_audio_start();\n    extern bool zw111_init(void);\n    zw111_init();"
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print("Patched main.c with zw111_init.")
        return

    if "tembed_display_start();" in content:
        content = content.replace(
            '#include "tembed_display.h"',
            '#include "tembed_display.h"\n#include "tembed_audio.h"'
        )
        content = content.replace(
            'tembed_display_start();',
            'tembed_display_start();\n    tembed_audio_start();'
        )
    else:
        target = "int app_main(void) {"
        replacement = """#include "tembed_display.h"
#include "tembed_audio.h"
#include "driver/gpio.h"
int app_main(void) {
    // 1. Enable Lilygo T-Embed Power Gate (GPIO 15) & Backlight (GPIO 21)
    gpio_reset_pin(GPIO_NUM_15);
    gpio_set_direction(GPIO_NUM_15, GPIO_MODE_OUTPUT);
    gpio_set_level(GPIO_NUM_15, 1);

    gpio_reset_pin(GPIO_NUM_21);
    gpio_set_direction(GPIO_NUM_21, GPIO_MODE_OUTPUT);
    gpio_set_level(GPIO_NUM_21, 1);

    // 2. Configure Rotary Encoder Center Button (GPIO 0) with internal pullup
    gpio_reset_pin(GPIO_NUM_0);
    gpio_set_direction(GPIO_NUM_0, GPIO_MODE_INPUT);
    gpio_pullup_en(GPIO_NUM_0);
    gpio_pulldown_dis(GPIO_NUM_0);

    // 3. Start LCD UI Task
    tembed_display_start();

    // 4. Start Kamen Rider Audio Task
    tembed_audio_start();

    // 5. Initialize ZW111 Fingerprint Sensor
    extern bool zw111_init(void);
    zw111_init();"""
        content = content.replace(target, replacement, 1)

    # Ensure pullup is maintained even when BOOT_PIN is initialized
    content = content.replace(
        "gpio_pulldown_dis(BOOT_PIN);",
        "gpio_pulldown_dis(BOOT_PIN);\n    gpio_pullup_en(BOOT_PIN);"
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched main.c")

TEMBED_BUTTON_C = '''/*
 * Customized button driver for LILYGO T-Embed (ESP32-S3)
 * Enforces strict physical rotary knob press with hardware pull-up & multi-sample debouncing
 */

#include "picokeys.h"
#include "button.h"
#include "led/led.h"
#include "pico_time.h"
#include "signal.h"
#include "usb.h"

#ifdef ESP_PLATFORM
#include "driver/gpio.h"
#endif

int (*button_pressed_cb)(uint8_t) = NULL;

bool req_button_pending = false;
static bool async_button_wait = false;
static uint32_t async_button_started = 0;
static uint32_t async_button_timeout = 0;
static uint32_t async_button_led_mode = MODE_MOUNTED;

bool is_req_button_pending(void) {
    return req_button_pending;
}

volatile bool cancel_button = false;
bool touch_accept_button = false;
volatile bool force_button_wait = true;

typedef enum {
    BTN_STAGE_WAIT_RELEASE = 0, // Must observe stable released (HIGH) state first
    BTN_STAGE_WAIT_PRESS,       // Armed and waiting for physical click
    BTN_STAGE_DEBOUNCE_PRESS,   // Click detected, verifying sustained press (> 40ms)
    BTN_STAGE_CONFIRMED,        // Confirmed genuine press! Complete on release or hold
} tembed_btn_stage_t;

static volatile tembed_btn_stage_t btn_stage = BTN_STAGE_WAIT_RELEASE;
static volatile uint32_t btn_stage_timer = 0;
static volatile uint32_t btn_press_start = 0;

static void tembed_button_hw_init(void) {
#ifdef ESP_PLATFORM
    gpio_config_t btn_cfg = {
        .pin_bit_mask = (1ULL << GPIO_NUM_0),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&btn_cfg);
#endif
}

static inline bool picok_board_button_read(void) {
#ifdef ESP_PLATFORM
    // 0 = pressed (LOW when knob clicked down), 1 = unpressed (HIGH with pullup)
    return (gpio_get_level(GPIO_NUM_0) == 0);
#else
    return false;
#endif
}

static uint32_t button_last_poll = 0;
volatile uint32_t button_pressed_duration = 0;
static bool button_pressed_state = false;
static uint32_t button_pressed_time = 0;
static uint8_t button_press = 0;
static bool button_hold_state = false;
static uint32_t button_hold_started = 0;

void button_wait_start(void) {
    button_wait_start_timeout(30);
}

void button_wait_start_timeout(uint32_t timeout_seconds) {
    if (timeout_seconds == 0) {
        timeout_seconds = 30;
    }
    force_button_wait = true;
    uint32_t button_timeout = timeout_seconds * 1000;

    tembed_button_hw_init();

    signal_user_presence_request_data_t data = {
        .timeout = button_timeout / 1000,
    };
    signal_emit_param(SIGNAL_USER_PRESENCE_REQUEST, &data);

    cancel_button = false;
    async_button_wait = true;
    async_button_started = board_millis();
    async_button_timeout = button_timeout;
    async_button_led_mode = led_get_mode();
    req_button_pending = true;
    led_set_mode(MODE_BUTTON);

    btn_stage = BTN_STAGE_WAIT_RELEASE;
    btn_stage_timer = 0;
    btn_press_start = 0;
}

void button_wait_poll(void) {
    if (!async_button_wait) {
        return;
    }

    uint32_t now = board_millis();
    bool pressed = picok_board_button_read();
    button_event_t result = BUTTON_EV_NONE;

    if (cancel_button) {
        result = BUTTON_EV_CANCELLED;
    }
    else if (async_button_started + async_button_timeout < now) {
        result = BUTTON_EV_TIMEOUT;
    }
    else {
        switch (btn_stage) {
            case BTN_STAGE_WAIT_RELEASE:
                // Require the button to be observed in unpressed (HIGH) state for at least 50ms before arming
                if (!pressed) {
                    if (btn_stage_timer == 0) {
                        btn_stage_timer = now;
                    } else if (now - btn_stage_timer >= 50) {
                        btn_stage = BTN_STAGE_WAIT_PRESS;
                        btn_stage_timer = 0;
                    }
                } else {
                    btn_stage_timer = 0;
                }
                break;

            case BTN_STAGE_WAIT_PRESS:
                // Armed and waiting for physical click of the knob!
                if (pressed) {
                    btn_stage = BTN_STAGE_DEBOUNCE_PRESS;
                    btn_press_start = now;
                }
                break;

            case BTN_STAGE_DEBOUNCE_PRESS:
                // Debounce: Verify the press stays LOW for at least 40ms (real human click)
                if (pressed) {
                    if (now - btn_press_start >= 40) {
                        btn_stage = BTN_STAGE_CONFIRMED;
                    }
                } else {
                    // Transient glitch (< 40ms), reject and return to waiting
                    btn_stage = BTN_STAGE_WAIT_PRESS;
                }
                break;

            case BTN_STAGE_CONFIRMED:
                // Genuine human click confirmed! Complete on release or if held for >= 250ms
                if (!pressed || (now - btn_press_start >= 250)) {
                    result = BUTTON_EV_PRESSED;
                }
                break;
        }
    }

    if (result == BUTTON_EV_NONE) {
        return;
    }

    async_button_wait = false;
    req_button_pending = false;
    led_set_mode(async_button_led_mode);

    uint32_t flag = 0;
    if (result == BUTTON_EV_PRESSED) {
        flag = EV_BUTTON_PRESSED;
        signal_emit(SIGNAL_USER_PRESENCE_COMPLETED);
    }
    else if (result == BUTTON_EV_TIMEOUT) {
        flag = EV_BUTTON_TIMEOUT;
        signal_emit(SIGNAL_USER_PRESENCE_TIMEOUT);
    }
    else {
        flag = EV_BUTTON_CANCELLED;
        signal_emit(SIGNAL_USER_PRESENCE_CANCELLED);
    }
    queue_try_add(&usb_to_card_q, &flag);
}

uint32_t button_timeout_seconds(void) {
    return 30;
}

void button_task(void) {
    uint32_t now = board_millis();
    if (now > 1000 && now - button_last_poll >= 10 && (async_button_wait || !is_busy())) {
        button_last_poll = now;
        if (async_button_wait) {
            button_wait_poll();
            return;
        }
        bool current_button_state = picok_board_button_read();
        if (current_button_state && !button_hold_state) {
            button_hold_started = now;
            button_pressed_duration = 0;
        }
        else if (!current_button_state && button_hold_state) {
            button_pressed_duration = button_hold_started == 0 ? 0 : now - button_hold_started;
            button_hold_started = 0;
        }
        else if (current_button_state && button_hold_state && button_hold_started != 0) {
            button_pressed_duration = now - button_hold_started;
        }
        button_hold_state = current_button_state;
        if (button_pressed_cb) {
            if (current_button_state != button_pressed_state) {
                if (current_button_state == false) {
                    if (button_pressed_time == 0 || button_pressed_time + 1000 > now) {
                        button_press++;
                    }
                    button_pressed_time = now;
                }
                button_pressed_state = current_button_state;
            }
            if (button_pressed_time > 0 && button_press > 0 && button_pressed_time + 1000 < now && button_pressed_state == false) {
                if (button_pressed_cb != NULL) {
                    (*button_pressed_cb)(button_press);
                }
                button_pressed_time = button_press = 0;
            }
        }
    }
}
'''

def patch_button_c():
    path = os.path.join(SDK_DIR, "src", "button.c")
    if not os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(TEMBED_BUTTON_C)
    print("Replaced button.c with robust T-Embed button driver")

def patch_led_c():
    path = os.path.join(SDK_DIR, "src", "led", "led.c")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Change ESP32-S3 default LED pin from GPIO 48 to GPIO 14 (Lilygo T-Embed WS2812 ring)
    content = content.replace("GPIO_NUM_48", "GPIO_NUM_14")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched led.c to use GPIO_NUM_14")

def patch_led_h():
    path = os.path.join(SDK_DIR, "src", "led", "led.h")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Set MODE_MOUNTED to LED_COLOR_OFF so LEDs stay off in idle
    content = content.replace(
        "MODE_MOUNTED     = (MAX_BTNESS << LED_BTNESS_SHIFT) | (LED_COLOR_GREEN << LED_COLOR_SHIFT)",
        "MODE_MOUNTED     = (MAX_BTNESS << LED_BTNESS_SHIFT) | (LED_COLOR_OFF << LED_COLOR_SHIFT)"
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched led.h for quiet idle mode")

def patch_led_neopixel():
    path = os.path.join(SDK_DIR, "src", "led", "led_neopixel.c")
    if not os.path.exists(path):
        return

    custom_code = '''/*
 * Customized for LILYGO T-Embed CC1101 Plus (ESP32-S3)
 */

#include "picokeys.h"
#include "led/led.h"
#include "signal.h"
#ifdef PICO_PLATFORM
#include "hardware/gpio.h"
#endif

#ifdef ESP_PLATFORM

#include "driver/gpio.h"
#include "neopixel.h"
#include "pico_time.h"

#define NEOPIXEL_COUNT 8
#define NEOPIXEL_PIN GPIO_NUM_14
#define BRIGHTNESS_SCALE 0.10f

tNeopixelContext neopixel = NULL;

typedef enum {
    TEMBED_LED_IDLE = 0,
    TEMBED_LED_WAITING,
    TEMBED_LED_SUCCESS,
    TEMBED_LED_TIMEOUT,
} tembed_led_state_t;

static volatile tembed_led_state_t led_ui_state = TEMBED_LED_IDLE;
static volatile uint32_t led_ui_until_ms = 0;

static inline uint32_t neopixel_rgb_ordered(uint8_t r, uint8_t g, uint8_t b) {
    return NP_RGB(r, g, b);
}

static int neopixel_signal_cb(signal_code_t code, void *data) {
    (void)data;
    uint32_t now = board_millis();
    switch (code) {
        case SIGNAL_USER_PRESENCE_REQUEST:
            led_ui_state = TEMBED_LED_WAITING;
            break;
        case SIGNAL_USER_PRESENCE_COMPLETED:
            led_ui_state = TEMBED_LED_SUCCESS;
            led_ui_until_ms = now + 3200; // 3.2 seconds matching Faiz Complete Henshin!
            break;
        case SIGNAL_USER_PRESENCE_TIMEOUT:
            led_ui_state = TEMBED_LED_TIMEOUT;
            led_ui_until_ms = now + 2000; // Solid red for 2 seconds
            break;
        case SIGNAL_USER_PRESENCE_CANCELLED:
        case SIGNAL_BOOT:
        case SIGNAL_USB_MOUNTED:
            led_ui_state = TEMBED_LED_IDLE;
            break;
        default:
            break;
    }
    return 0;
}

void led_driver_init_neopixel(void) {
    neopixel = neopixel_Init(NEOPIXEL_COUNT, NEOPIXEL_PIN);
    signal_add(SIGNAL_USER_PRESENCE_REQUEST, SIGNAL_FLAG_NONE, neopixel_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_COMPLETED, SIGNAL_FLAG_NONE, neopixel_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_TIMEOUT, SIGNAL_FLAG_NONE, neopixel_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_CANCELLED, SIGNAL_FLAG_NONE, neopixel_signal_cb);
    signal_add(SIGNAL_BOOT, SIGNAL_FLAG_NONE, neopixel_signal_cb);
    signal_add(SIGNAL_USB_MOUNTED, SIGNAL_FLAG_NONE, neopixel_signal_cb);
}

void led_driver_color_neopixel(uint8_t color, uint32_t led_brightness, float progress) {
    static tNeopixel spx[NEOPIXEL_COUNT];
    if (neopixel == NULL) return;

    uint32_t now = board_millis();

    // 1. User pressed button / verified: Faiz Complete Photon Burst -> Solid Green!
    if (led_ui_state == TEMBED_LED_SUCCESS) {
        if (now < led_ui_until_ms) {
            uint32_t remaining = led_ui_until_ms - now;
            uint32_t elapsed = (remaining <= 3200) ? (3200 - remaining) : 0;
            if (elapsed < 600) {
                // High-speed rotating crimson / gold photon comet!
                int head = (elapsed / 40) % NEOPIXEL_COUNT;
                for (int i = 0; i < NEOPIXEL_COUNT; i++) {
                    spx[i].index = i;
                    if (i == head) {
                        spx[i].rgb = neopixel_rgb_ordered(255, 220, 0); // Gold head
                    } else if (i == (head + 1) % NEOPIXEL_COUNT || i == (head - 1 + NEOPIXEL_COUNT) % NEOPIXEL_COUNT) {
                        spx[i].rgb = neopixel_rgb_ordered(255, 0, 0); // Crimson tail
                    } else {
                        spx[i].rgb = neopixel_rgb_ordered(30, 0, 0);
                    }
                }
            } else {
                uint8_t g = (uint8_t)(255.0f * BRIGHTNESS_SCALE);
                for (int i = 0; i < NEOPIXEL_COUNT; i++) {
                    spx[i].index = i;
                    spx[i].rgb = neopixel_rgb_ordered(0, g, 0);
                }
            }
            neopixel_SetPixel(neopixel, spx, NEOPIXEL_COUNT);
            return;
        } else {
            led_ui_state = TEMBED_LED_IDLE;
        }
    }

    // 2. Request timed out: Solid RED for 2.0s!
    if (led_ui_state == TEMBED_LED_TIMEOUT) {
        if (now < led_ui_until_ms) {
            uint8_t r = (uint8_t)(255.0f * BRIGHTNESS_SCALE);
            for (int i = 0; i < NEOPIXEL_COUNT; i++) {
                spx[i].index = i;
                spx[i].rgb = neopixel_rgb_ordered(r, 0, 0);
            }
            neopixel_SetPixel(neopixel, spx, NEOPIXEL_COUNT);
            return;
        } else {
            led_ui_state = TEMBED_LED_IDLE;
        }
    }

    uint32_t mode = led_get_mode();

    // 3. Waiting for user presence / button press: Clockwise rotating amber comet!
    if (led_ui_state == TEMBED_LED_WAITING || mode == MODE_BUTTON || color == LED_COLOR_YELLOW) {
        // Clockwise rotation around the dial: 7 -> 6 -> 5 -> 4 -> 3 -> 2 -> 1 -> 0
        int head = (NEOPIXEL_COUNT - 1) - ((now / 70) % NEOPIXEL_COUNT);
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            int dist = (i - head + NEOPIXEL_COUNT) % NEOPIXEL_COUNT;
            float b = 0.0f;
            if (dist == 0) b = 1.0f;
            else if (dist == 1) b = 0.5f;
            else if (dist == 2) b = 0.2f;
            else if (dist == 3) b = 0.05f;

            uint8_t r = (uint8_t)(255.0f * b * BRIGHTNESS_SCALE);
            uint8_t g = (uint8_t)(140.0f * b * BRIGHTNESS_SCALE);
            uint8_t bl = 0;
            spx[i].index = i;
            spx[i].rgb = neopixel_rgb_ordered(r, g, bl);
        }
        neopixel_SetPixel(neopixel, spx, NEOPIXEL_COUNT);
        return;
    }

    // 4. Idle mode / USB Mounted / Off: All 8 LEDs completely OFF (clean & calm)
    for (int i = 0; i < NEOPIXEL_COUNT; i++) {
        spx[i].index = i;
        spx[i].rgb = 0;
    }
    neopixel_SetPixel(neopixel, spx, NEOPIXEL_COUNT);
}

led_driver_t led_driver_neopixel = {
    .init = led_driver_init_neopixel,
    .set_color = led_driver_color_neopixel,
};

#endif
'''
    with open(path, "w", encoding="utf-8") as f:
        f.write(custom_code)
    print("Patched led_neopixel.c")

def patch_partitions():
    path = os.path.join(SDK_DIR, "config", "esp32", "partitions.csv")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Increase factory partition from 1M to 1800K (fits Kamen Rider UI)
    content = content.replace("factory,  app,  factory,  0x20000, 1M", "factory,  app,  factory,  0x20000, 1800K")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched partitions.csv to 1800K")

def patch_cmake():
    p = os.path.join(SDK_DIR, "picokeys_sdk_import.cmake")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            c = f.read()
        if "tembed_display.c" not in c:
            c += "\nlist(APPEND PICOKEYS_SOURCES ${CMAKE_CURRENT_LIST_DIR}/src/tembed_display.c)\n"
        if "tembed_audio.c" not in c:
            c += "\nlist(APPEND PICOKEYS_SOURCES ${CMAKE_CURRENT_LIST_DIR}/src/tembed_audio.c)\n"
        with open(p, "w", encoding="utf-8") as f:
            f.write(c)
        print("Appended tembed_display.c and tembed_audio.c to PICOKEYS_SOURCES")

if __name__ == "__main__":
    print("Starting LILYGO T-Embed patch...")
    copy_images()
    copy_audio()
    copy_eye_data()
    create_display_files()
    create_audio_files()
    patch_main_c()
    patch_button_c()
    patch_led_c()
    patch_led_h()
    patch_led_neopixel()
    patch_partitions()
    patch_cmake()
    print("All patches successfully applied.")
