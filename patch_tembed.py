#!/usr/bin/env python3
"""
patch_tembed.py
Automated patch script for LILYGO T-Embed CC1101 Plus (ESP32-S3)
Applies complete hardware support for:
1. Power Gate (GPIO 15) & Screen Backlight (GPIO 21)
2. Rotary encoder center button presence (GPIO 0 with internal pullup)
3. 8x WS2812 NeoPixel ring on GPIO 14 with rotating dial animation
4. ST7789 170x320 IPS display with live FIDO2 status UI
"""

import os

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
#include <string.h>
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

#define SCREEN_W 320
#define SCREEN_H 170
#define OFFSET_X 0
#define OFFSET_Y 35

// RGB565 Colors
#define COLOR_BLACK       0x0000
#define COLOR_NAVY        0x0010
#define COLOR_DARKBLUE    0x0115
#define COLOR_WHITE       0xFFFF
#define COLOR_RED         0xF800
#define COLOR_GREEN       0x07E0
#define COLOR_CYAN        0x07FF
#define COLOR_YELLOW      0xFFE0
#define COLOR_ORANGE      0xFD20
#define COLOR_GRAY        0x7BEF
#define COLOR_DARKGRAY    0x31A6

static spi_device_handle_t spi_lcd = NULL;
static QueueHandle_t ui_queue = NULL;

static const uint8_t font8x8[95][8] = {
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00}, // 32 ' '
    {0x18,0x3C,0x3C,0x18,0x18,0x00,0x18,0x00}, // 33 '!'
    {0x66,0x66,0x24,0x00,0x00,0x00,0x00,0x00}, // 34 '"'
    {0x6C,0x6C,0xFE,0x6C,0xFE,0x6C,0x6C,0x00}, // 35 '#'
    {0x18,0x3E,0x60,0x3C,0x06,0x7C,0x18,0x00}, // 36 '$'
    {0x00,0xC6,0xCC,0x18,0x30,0x66,0xC6,0x00}, // 37 '%'
    {0x38,0x6C,0x38,0x76,0xDC,0xCC,0x76,0x00}, // 38 '&'
    {0x18,0x18,0x30,0x00,0x00,0x00,0x00,0x00}, // 39 '''
    {0x0C,0x18,0x30,0x30,0x30,0x18,0x0C,0x00}, // 40 '('
    {0x30,0x18,0x0C,0x0C,0x0C,0x18,0x30,0x00}, // 41 ')'
    {0x00,0x66,0x3C,0xFF,0x3C,0x66,0x00,0x00}, // 42 '*'
    {0x00,0x18,0x18,0x7E,0x18,0x18,0x00,0x00}, // 43 '+'
    {0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x30}, // 44 ','
    {0x00,0x00,0x00,0x7E,0x00,0x00,0x00,0x00}, // 45 '-'
    {0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x00}, // 46 '.'
    {0x06,0x0C,0x18,0x30,0x60,0xC0,0x80,0x00}, // 47 '/'
    {0x7C,0xC6,0xCE,0xD6,0xE6,0xC6,0x7C,0x00}, // 48 '0'
    {0x18,0x38,0x18,0x18,0x18,0x18,0x7E,0x00}, // 49 '1'
    {0x7C,0xC6,0x06,0x1C,0x30,0x66,0xFE,0x00}, // 50 '2'
    {0x7C,0xC6,0x06,0x3C,0x06,0xC6,0x7C,0x00}, // 51 '3'
    {0x1C,0x3C,0x6C,0xCC,0xFE,0x0C,0x1E,0x00}, // 52 '4'
    {0xFE,0xC0,0xFC,0x06,0x06,0xC6,0x7C,0x00}, // 53 '5'
    {0x38,0x60,0xC0,0xFC,0xC6,0xC6,0x7C,0x00}, // 54 '6'
    {0xFE,0xC6,0x0C,0x18,0x30,0x30,0x30,0x00}, // 55 '7'
    {0x7C,0xC6,0xC6,0x7C,0xC6,0xC6,0x7C,0x00}, // 56 '8'
    {0x7C,0xC6,0xC6,0x7E,0x06,0x0C,0x78,0x00}, // 57 '9'
    {0x00,0x18,0x18,0x00,0x00,0x18,0x18,0x00}, // 58 ':'
    {0x00,0x18,0x18,0x00,0x00,0x18,0x18,0x30}, // 59 ';'
    {0x06,0x0C,0x18,0x30,0x18,0x0C,0x06,0x00}, // 60 '<'
    {0x00,0x00,0x7E,0x00,0x7E,0x00,0x00,0x00}, // 61 '='
    {0x60,0x30,0x18,0x0C,0x18,0x30,0x60,0x00}, // 62 '>'
    {0x7C,0xC6,0x0C,0x18,0x18,0x00,0x18,0x00}, // 63 '?'
    {0x7C,0xC6,0xDE,0xDE,0xDC,0xC0,0x7C,0x00}, // 64 '@'
    {0x38,0x6C,0xC6,0xFE,0xC6,0xC6,0xC6,0x00}, // 65 'A'
    {0xFC,0x66,0x66,0x7C,0x66,0x66,0xFC,0x00}, // 66 'B'
    {0x3C,0x66,0xC0,0xC0,0xC0,0x66,0x3C,0x00}, // 67 'C'
    {0xF8,0x6C,0x66,0x66,0x66,0x6C,0xF8,0x00}, // 68 'D'
    {0xFE,0x62,0x68,0x78,0x68,0x62,0xFE,0x00}, // 69 'E'
    {0xFE,0x62,0x68,0x78,0x68,0x60,0xF0,0x00}, // 70 'F'
    {0x3C,0x66,0xC0,0xC0,0xCE,0x66,0x3E,0x00}, // 71 'G'
    {0xC6,0xC6,0xC6,0xFE,0xC6,0xC6,0xC6,0x00}, // 72 'H'
    {0x7E,0x18,0x18,0x18,0x18,0x18,0x7E,0x00}, // 73 'I'
    {0x1E,0x0C,0x0C,0x0C,0xCC,0xCC,0x78,0x00}, // 74 'J'
    {0xE6,0x66,0x6C,0x78,0x6C,0x66,0xE6,0x00}, // 75 'K'
    {0xF0,0x60,0x60,0x60,0x62,0x66,0xFE,0x00}, // 76 'L'
    {0xC6,0xEE,0xFE,0xFE,0xD6,0xC6,0xC6,0x00}, // 77 'M'
    {0xC6,0xE6,0xF6,0xFE,0xDE,0xCE,0xC6,0x00}, // 78 'N'
    {0x7C,0xC6,0xC6,0xC6,0xC6,0xC6,0x7C,0x00}, // 79 'O'
    {0xFC,0x66,0x66,0x7C,0x60,0x60,0xF0,0x00}, // 80 'P'
    {0x7C,0xC6,0xC6,0xC6,0xD6,0xDE,0x7C,0x0E}, // 81 'Q'
    {0xFC,0x66,0x66,0x7C,0x6C,0x66,0xE6,0x00}, // 82 'R'
    {0x7C,0xC6,0x60,0x38,0x0C,0xC6,0x7C,0x00}, // 83 'S'
    {0x7E,0x5A,0x18,0x18,0x18,0x18,0x3C,0x00}, // 84 'T'
    {0xC6,0xC6,0xC6,0xC6,0xC6,0xC6,0x7C,0x00}, // 85 'U'
    {0xC6,0xC6,0xC6,0xC6,0xC6,0x6C,0x38,0x00}, // 86 'V'
    {0xC6,0xC6,0xC6,0xD6,0xFE,0xEE,0xC6,0x00}, // 87 'W'
    {0xC6,0xC6,0x6C,0x38,0x6C,0xC6,0xC6,0x00}, // 88 'X'
    {0x66,0x66,0x66,0x3C,0x18,0x18,0x3C,0x00}, // 89 'Y'
    {0xFE,0xC6,0x0C,0x18,0x30,0x62,0xFE,0x00}, // 90 'Z'
    {0x3C,0x30,0x30,0x30,0x30,0x30,0x3C,0x00}, // 91 '['
    {0xC0,0x60,0x30,0x18,0x0C,0x06,0x02,0x00}, // 92 '\'
    {0x3C,0x0C,0x0C,0x0C,0x0C,0x0C,0x3C,0x00}, // 93 ']'
    {0x10,0x38,0x6C,0xC6,0x00,0x00,0x00,0x00}, // 94 '^'
    {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xFF}, // 95 '_'
    {0x30,0x18,0x0C,0x00,0x00,0x00,0x00,0x00}, // 96 '`'
    {0x00,0x00,0x78,0x0C,0x7C,0xCC,0x76,0x00}, // 97 'a'
    {0xE0,0x60,0x7C,0x66,0x66,0x66,0xDC,0x00}, // 98 'b'
    {0x00,0x00,0x78,0xCC,0xC0,0xCC,0x78,0x00}, // 99 'c'
    {0x1C,0x0C,0x7C,0xCC,0xCC,0xCC,0x76,0x00}, // 100 'd'
    {0x00,0x00,0x78,0xCC,0xFC,0xC0,0x78,0x00}, // 101 'e'
    {0x38,0x6C,0x60,0xF0,0x60,0x60,0xF0,0x00}, // 102 'f'
    {0x00,0x00,0x76,0xCC,0xCC,0x7C,0x0C,0xF8}, // 103 'g'
    {0xE0,0x60,0x6C,0x76,0x66,0x66,0xE6,0x00}, // 104 'h'
    {0x18,0x00,0x38,0x18,0x18,0x18,0x3C,0x00}, // 105 'i'
    {0x06,0x00,0x0E,0x06,0x06,0x66,0x66,0x3C}, // 106 'j'
    {0xE0,0x60,0x66,0x6C,0x78,0x6C,0xE6,0x00}, // 107 'k'
    {0x38,0x18,0x18,0x18,0x18,0x18,0x3C,0x00}, // 108 'l'
    {0x00,0x00,0xEC,0xFE,0xD6,0xD6,0xD6,0x00}, // 109 'm'
    {0x00,0x00,0xDC,0x66,0x66,0x66,0x66,0x00}, // 110 'n'
    {0x00,0x00,0x7C,0xC6,0xC6,0xC6,0x7C,0x00}, // 111 'o'
    {0x00,0x00,0xDC,0x66,0x66,0x7C,0x60,0xF0}, // 112 'p'
    {0x00,0x00,0x76,0xCC,0xCC,0x7C,0x0C,0x1E}, // 113 'q'
    {0x00,0x00,0xDC,0x76,0x60,0x60,0xF0,0x00}, // 114 'r'
    {0x00,0x00,0x7C,0xC0,0x78,0x06,0xFC,0x00}, // 115 's'
    {0x30,0x30,0xFC,0x30,0x30,0x34,0x18,0x00}, // 116 't'
    {0x00,0x00,0xCC,0xCC,0xCC,0xCC,0x76,0x00}, // 117 'u'
    {0x00,0x00,0xC6,0xC6,0xC6,0x6C,0x38,0x00}, // 118 'v'
    {0x00,0x00,0xC6,0xD6,0xFE,0xFE,0x6C,0x00}, // 119 'w'
    {0x00,0x00,0xC6,0x6C,0x38,0x6C,0xC6,0x00}, // 120 'x'
    {0x00,0x00,0xC6,0xC6,0xC6,0x7E,0x06,0xFC}, // 121 'y'
    {0x00,0x00,0x7E,0x4C,0x18,0x32,0x7E,0x00}, // 122 'z'
    {0x0E,0x18,0x18,0x70,0x18,0x18,0x0E,0x00}, // 123 '{'
    {0x18,0x18,0x18,0x18,0x18,0x18,0x18,0x00}, // 124 '|'
    {0x70,0x18,0x18,0x0E,0x18,0x18,0x70,0x00}, // 125 '}'
    {0x76,0xDC,0x00,0x00,0x00,0x00,0x00,0x00}  // 126 '~'
};

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

static void st7789_fill_rect(uint16_t x0, uint16_t y0, uint16_t w, uint16_t h, uint16_t color) {
    if (x0 >= SCREEN_W || y0 >= SCREEN_H || w == 0 || h == 0) return;
    if (x0 + w > SCREEN_W) w = SCREEN_W - x0;
    if (y0 + h > SCREEN_H) h = SCREEN_H - y0;

    st7789_set_window(x0, y0, x0 + w - 1, y0 + h - 1);

    uint8_t byte_hi = color >> 8;
    uint8_t byte_lo = color & 0xFF;
    uint16_t swapped = (byte_lo << 8) | byte_hi;

    static uint16_t line_buf[SCREEN_W];
    for (int i = 0; i < w; i++) line_buf[i] = swapped;

    for (int y = 0; y < h; y++) {
        lcd_data((uint8_t*)line_buf, w * 2);
    }
}

static void st7789_draw_char(uint16_t x, uint16_t y, char c, uint16_t fg, uint16_t bg, uint8_t scale) {
    if (c < 32 || c > 126) c = ' ';
    const uint8_t *bitmap = font8x8[c - 32];

    for (int col = 0; col < 8; col++) {
        uint8_t line = bitmap[col];
        for (int bit = 0; bit < 8; bit++) {
            uint16_t color = (line & (1 << (7 - bit))) ? fg : bg;
            if (scale == 1) {
                st7789_fill_rect(x + bit, y + col, 1, 1, color);
            } else {
                st7789_fill_rect(x + bit * scale, y + col * scale, scale, scale, color);
            }
        }
    }
}

static void st7789_draw_string(uint16_t x, uint16_t y, const char *str, uint16_t fg, uint16_t bg, uint8_t scale) {
    uint16_t cur_x = x;
    while (*str) {
        st7789_draw_char(cur_x, y, *str, fg, bg, scale);
        cur_x += 8 * scale;
        str++;
    }
}

static void st7789_draw_string_centered(uint16_t y, const char *str, uint16_t fg, uint16_t bg, uint8_t scale) {
    int len = strlen(str);
    int total_w = len * 8 * scale;
    int x = (SCREEN_W - total_w) / 2;
    if (x < 0) x = 0;
    st7789_draw_string(x, y, str, fg, bg, scale);
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
        .max_transfer_sz = SCREEN_W * 2 + 8,
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

    lcd_cmd(0x36);
    lcd_data_byte(0x70);

    lcd_cmd(0x21);
    lcd_cmd(0x13);
    vTaskDelay(pdMS_TO_TICKS(10));

    lcd_cmd(0x29);
    vTaskDelay(pdMS_TO_TICKS(50));
}

static void tembed_draw_screen(tembed_ui_state_t state) {
    switch (state) {
        case TEMBED_UI_READY:
            st7789_fill_rect(0, 0, SCREEN_W, SCREEN_H, COLOR_NAVY);
            st7789_fill_rect(0, 0, SCREEN_W, 28, COLOR_DARKBLUE);
            st7789_draw_string_centered(8, "LILYGO T-EMBED FIDO2", COLOR_CYAN, COLOR_DARKBLUE, 2);

            st7789_fill_rect(20, 48, SCREEN_W - 40, 60, COLOR_DARKGRAY);
            st7789_draw_string_centered(60, "KEY READY", COLOR_GREEN, COLOR_DARKGRAY, 2);
            st7789_draw_string_centered(86, "Security Key Active", COLOR_WHITE, COLOR_DARKGRAY, 1);

            st7789_draw_string_centered(128, "Waiting for WebAuthn request...", COLOR_GRAY, COLOR_NAVY, 1);
            st7789_draw_string_centered(146, "Press dial to test presence", COLOR_GRAY, COLOR_NAVY, 1);
            break;

        case TEMBED_UI_WAIT_USER:
            st7789_fill_rect(0, 0, SCREEN_W, SCREEN_H, COLOR_BLACK);
            st7789_fill_rect(0, 0, SCREEN_W, 28, COLOR_ORANGE);
            st7789_draw_string_centered(8, "! TOUCH REQUIRED !", COLOR_BLACK, COLOR_ORANGE, 2);

            st7789_fill_rect(10, 42, SCREEN_W - 20, 80, COLOR_DARKGRAY);
            st7789_draw_string_centered(54, "USER PRESENCE", COLOR_YELLOW, COLOR_DARKGRAY, 2);
            st7789_draw_string_centered(82, ">> PRESS DIAL BUTTON <<", COLOR_WHITE, COLOR_DARKGRAY, 2);

            st7789_draw_string_centered(138, "LED Ring Spinning...", COLOR_YELLOW, COLOR_BLACK, 1);
            break;

        case TEMBED_UI_SUCCESS:
            st7789_fill_rect(0, 0, SCREEN_W, SCREEN_H, COLOR_NAVY);
            st7789_fill_rect(0, 0, SCREEN_W, 28, COLOR_GREEN);
            st7789_draw_string_centered(8, "AUTHENTICATED", COLOR_BLACK, COLOR_GREEN, 2);

            st7789_fill_rect(20, 50, SCREEN_W - 40, 60, COLOR_DARKBLUE);
            st7789_draw_string_centered(64, "ACCESS GRANTED!", COLOR_GREEN, COLOR_DARKBLUE, 2);
            st7789_draw_string_centered(90, "Presence Verified", COLOR_WHITE, COLOR_DARKBLUE, 1);

            st7789_draw_string_centered(138, "Returning to PC...", COLOR_CYAN, COLOR_NAVY, 1);
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

    while (1) {
        if (xQueueReceive(ui_queue, &new_state, portMAX_DELAY)) {
            if (new_state != current_state) {
                current_state = new_state;
                tembed_draw_screen(current_state);
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
    (void)data;
    switch (code) {
        case SIGNAL_BOOT:
        case SIGNAL_USB_MOUNTED:
            tembed_display_set_state(TEMBED_UI_READY);
            break;
        case SIGNAL_USER_PRESENCE_REQUEST:
            tembed_display_set_state(TEMBED_UI_WAIT_USER);
            break;
        case SIGNAL_USER_PRESENCE_COMPLETED:
            tembed_display_set_state(TEMBED_UI_SUCCESS);
            break;
        case SIGNAL_USER_PRESENCE_CANCELLED:
        case SIGNAL_USER_PRESENCE_TIMEOUT:
            tembed_display_set_state(TEMBED_UI_READY);
            break;
        default:
            break;
    }
    return 0;
}

void tembed_display_start(void) {
    ui_queue = xQueueCreate(8, sizeof(tembed_ui_state_t));
    xTaskCreate(tembed_ui_task, "tembed_ui", 3072, NULL, 1, NULL);

    signal_add(SIGNAL_BOOT, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USB_MOUNTED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_REQUEST, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_COMPLETED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_CANCELLED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_TIMEOUT, SIGNAL_FLAG_NONE, tembed_signal_cb);
}
"""

def create_display_files():
    src_dir = os.path.join(SDK_DIR, "src")
    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "tembed_display.h"), "w", encoding="utf-8") as f:
        f.write(TEMBED_DISPLAY_H)
    with open(os.path.join(src_dir, "tembed_display.c"), "w", encoding="utf-8") as f:
        f.write(TEMBED_DISPLAY_C)
    print("Created tembed_display.h and tembed_display.c")

def patch_main_c():
    path = os.path.join(SDK_DIR, "src", "main.c")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if "tembed_display_start" in content:
        print("main.c already patched.")
        return

    target = "int app_main(void) {"
    replacement = """#include "tembed_display.h"
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
    tembed_display_start();"""
    content = content.replace(target, replacement, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched main.c")

def patch_led_c():
    path = os.path.join(SDK_DIR, "src", "led", "led.c")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    content = content.replace("GPIO_NUM_48", "GPIO_NUM_14")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched led.c")

def patch_led_neopixel():
    path = os.path.join(SDK_DIR, "src", "led", "led_neopixel.c")
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if "Rotating spinner animation around the rotary encoder dial!" in content:
        print("led_neopixel.c already patched.")
        return

    custom_code = '''/*
 * Customized for LILYGO T-Embed CC1101 Plus (ESP32-S3)
 */

#include "picokeys.h"
#include "led/led.h"
#ifdef PICO_PLATFORM
#include "hardware/gpio.h"
#endif

#ifdef ESP_PLATFORM

#include "driver/gpio.h"
#include "neopixel.h"
#include "pico_time.h"

#define NEOPIXEL_COUNT 8

tNeopixelContext neopixel = NULL;

tNeopixel pixel[] = {
    { 0, NP_RGB(0,  0,  0) }, /* off */
    { 0, NP_RGB(255,  0, 0) }, /* red */
    { 0, NP_RGB(0, 255,  0) }, /* green */
    { 0, NP_RGB(0, 0,  255) }, /* blue */
    { 0, NP_RGB(255,  255, 0) }, /* yellow */
    { 0, NP_RGB(255,  0, 255) }, /* magenta */
    { 0, NP_RGB(0, 255,  255) }, /* cyan */
    { 0, NP_RGB(255, 255,  255) }, /* white */
};

static inline uint32_t neopixel_rgb_ordered(uint8_t r, uint8_t g, uint8_t b) {
    switch (phy_data.led_order) {
        case PHY_LED_ORDER_RGB:
            return NP_RGB(r, g, b);
        case PHY_LED_ORDER_RBG:
            return NP_RGB(r, b, g);
        case PHY_LED_ORDER_GBR:
            return NP_RGB(g, b, r);
        case PHY_LED_ORDER_BRG:
            return NP_RGB(b, r, g);
        case PHY_LED_ORDER_BGR:
            return NP_RGB(b, g, r);
        case PHY_LED_ORDER_GRB:
        default:
            return NP_RGB(g, r, b); // Default to GRB for WS2812B LEDs
    }
}

#if defined(CONFIG_IDF_TARGET_ESP32S3)
    #define NEOPIXEL_PIN GPIO_NUM_14
#elif defined(CONFIG_IDF_TARGET_ESP32S2)
    #define NEOPIXEL_PIN GPIO_NUM_15
#elif defined(CONFIG_IDF_TARGET_ESP32C6)
    #define NEOPIXEL_PIN GPIO_NUM_8
#else
    #define NEOPIXEL_PIN GPIO_NUM_27
#endif

void led_driver_init_neopixel(void) {
    uint8_t gpio = NEOPIXEL_PIN;
    if (phy_data.led_gpio_present) {
        gpio = phy_data.led_gpio;
    }
    neopixel = neopixel_Init(NEOPIXEL_COUNT, gpio);
}

void led_driver_color_neopixel(uint8_t color, uint32_t led_brightness, float progress) {
    static tNeopixel spx[NEOPIXEL_COUNT];
    if (!(phy_data.opts & PHY_OPT_DIMM)) {
        progress = progress >= 0.5 ? 1 : 0;
    }
    uint32_t led_phy_btness = phy_data.led_brightness_present ? phy_data.led_brightness : MAX_BTNESS;
    float brightness = ((float)led_brightness / MAX_BTNESS) * ((float)led_phy_btness / MAX_BTNESS) * progress;

    if (color == LED_COLOR_OFF) {
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            spx[i].index = i;
            spx[i].rgb = 0;
        }
        neopixel_SetPixel(neopixel, spx, NEOPIXEL_COUNT);
        return;
    }

    if (color == LED_COLOR_YELLOW) {
        uint32_t now = board_millis();
        int head = (now / 75) % NEOPIXEL_COUNT;
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            int dist = (head - i + NEOPIXEL_COUNT) % NEOPIXEL_COUNT;
            float b = 0.0f;
            if (dist == 0) b = 1.0f;
            else if (dist == 1) b = 0.45f;
            else if (dist == 2) b = 0.15f;
            else if (dist == 3) b = 0.04f;

            uint8_t r = (uint8_t)(255 * b);
            uint8_t g = (uint8_t)(160 * b);
            uint8_t bl = 0;
            spx[i].index = i;
            spx[i].rgb = neopixel_rgb_ordered(r, g, bl);
        }
        neopixel_SetPixel(neopixel, spx, NEOPIXEL_COUNT);
        return;
    }

    if (color == LED_COLOR_GREEN) {
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            spx[i].index = i;
            spx[i].rgb = neopixel_rgb_ordered(0, 255, 0);
        }
        neopixel_SetPixel(neopixel, spx, NEOPIXEL_COUNT);
        return;
    }

    uint32_t pixel_color = pixel[color].rgb;
    uint8_t r = (pixel_color >> 16) & 0xFF;
    uint8_t g = (pixel_color >> 8) & 0xFF;
    uint8_t b = (pixel_color) & 0xFF;

    r = (uint8_t)(r * brightness);
    g = (uint8_t)(g * brightness);
    b = (uint8_t)(b * brightness);
    uint32_t ordered = neopixel_rgb_ordered(r, g, b);

    for (int i = 0; i < NEOPIXEL_COUNT; i++) {
        spx[i].index = i;
        spx[i].rgb = ordered;
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

def patch_cmake():
    p = os.path.join(SDK_DIR, "picokeys_sdk_import.cmake")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            c = f.read()
        if "tembed_display.c" not in c:
            c += "\nlist(APPEND PICOKEYS_SOURCES ${CMAKE_CURRENT_LIST_DIR}/src/tembed_display.c)\n"
            with open(p, "w", encoding="utf-8") as f:
                f.write(c)
            print("Appended tembed_display.c to PICOKEYS_SOURCES")

if __name__ == "__main__":
    print("Starting LILYGO T-Embed patch...")
    create_display_files()
    patch_main_c()
    patch_led_c()
    patch_led_neopixel()
    patch_cmake()
    print("All patches successfully applied.")
