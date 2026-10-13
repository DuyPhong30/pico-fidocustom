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

#define SCREEN_W 170
#define SCREEN_H 320
#define OFFSET_X 35
#define OFFSET_Y 0

static spi_device_handle_t spi_lcd = NULL;
static QueueHandle_t ui_queue = NULL;

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

    while (1) {
        TickType_t wait_ticks = portMAX_DELAY;
        if (current_state == TEMBED_UI_SUCCESS || current_state == TEMBED_UI_ERROR) {
            wait_ticks = pdMS_TO_TICKS(2000); // Keep Confirmed / Timeout card for 2.0s
        }

        if (xQueueReceive(ui_queue, &new_state, wait_ticks)) {
            if (new_state != current_state) {
                current_state = new_state;
                tembed_draw_screen(current_state);
            }
        } else {
            // After 2.0s in SUCCESS or ERROR, return to READY (sleeping cat)
            if (current_state == TEMBED_UI_SUCCESS || current_state == TEMBED_UI_ERROR) {
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
    xTaskCreate(tembed_ui_task, "tembed_ui", 3072, NULL, 1, NULL);

    signal_add(SIGNAL_BOOT, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USB_MOUNTED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_REQUEST, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_COMPLETED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_CANCELLED, SIGNAL_FLAG_NONE, tembed_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_TIMEOUT, SIGNAL_FLAG_NONE, tembed_signal_cb);
}
"""

def copy_images():
    src = os.path.join(ROOT, "tembed_images.h")
    dst = os.path.join(SDK_DIR, "src", "tembed_images.h")
    if os.path.exists(src):
        shutil.copy(src, dst)
        print("Copied tembed_images.h to SDK")

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

    # Ensure pullup is maintained even when BOOT_PIN is initialized
    content = content.replace(
        "gpio_pulldown_dis(BOOT_PIN);",
        "gpio_pulldown_dis(BOOT_PIN);\n    gpio_pullup_en(BOOT_PIN);"
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched main.c")

def patch_button_c():
    path = os.path.join(SDK_DIR, "src", "button.c")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Force button timeout to 30s
    content = content.replace(
        "return phy_data.up_btn_present ? phy_data.up_btn : 0;",
        "return 30;"
    )

    # 2. In button_wait_start_timeout, force button wait unconditionally
    target_start = "void button_wait_start_timeout(uint32_t timeout_seconds) {"
    replacement_start = """void button_wait_start_timeout(uint32_t timeout_seconds) {
    force_button_wait = true;
    if (timeout_seconds == 0) timeout_seconds = 30;"""
    content = content.replace(target_start, replacement_start, 1)

    # 3. Reset async_button_pressed to false initially
    content = content.replace(
        "async_button_pressed = picok_board_button_read();",
        "async_button_pressed = false;"
    )

    # 4. In button_wait_poll, trigger immediately when rotary knob button is pressed
    content = content.replace(
        "else if (async_button_pressed && !pressed) {",
        "else if (pressed || (async_button_pressed && !pressed)) {"
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched button.c")

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
            led_ui_until_ms = now + 2000; // Solid green for 2 seconds
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

    // 1. User pressed button / verified: Solid GREEN for 2.0s!
    if (led_ui_state == TEMBED_LED_SUCCESS) {
        if (now < led_ui_until_ms) {
            uint8_t g = (uint8_t)(255.0f * BRIGHTNESS_SCALE);
            for (int i = 0; i < NEOPIXEL_COUNT; i++) {
                spx[i].index = i;
                spx[i].rgb = neopixel_rgb_ordered(0, g, 0);
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
    copy_images()
    create_display_files()
    patch_main_c()
    patch_button_c()
    patch_led_c()
    patch_led_h()
    patch_led_neopixel()
    patch_cmake()
    print("All patches successfully applied.")
