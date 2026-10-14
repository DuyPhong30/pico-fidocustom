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

TEMBED_AUDIO_H = """/*
 * LILYGO T-Embed Audio Driver for FIDO2 Security Key
 * Chip: MAX98357A I2S Class-D DAC/Amp
 * Pins: BCLK = GPIO 46, LRCLK/WS = GPIO 40, DOUT = GPIO 7
 */

#ifndef _TEMBED_AUDIO_H_
#define _TEMBED_AUDIO_H_

#include <stdint.h>

typedef enum {
    TEMBED_SOUND_NONE = 0,
    TEMBED_SOUND_WAITING,   // Faiz "Standing By"
    TEMBED_SOUND_CONFIRMED, // Faiz "COMPLETE!"
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

#define I2S_BCLK_PIN GPIO_NUM_40
#define I2S_WS_PIN   GPIO_NUM_7
#define I2S_DOUT_PIN GPIO_NUM_6
#define I2S_SD_PIN   GPIO_NUM_46

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
#include "driver/i2s_std.h"
static i2s_chan_handle_t tx_chan = NULL;
#else
#include "driver/i2s.h"
#endif

static QueueHandle_t audio_queue = NULL;

static void tembed_i2s_init(void) {
    // 1. Enable MAX98357A amplifier via SD_MODE (GPIO 46)
    gpio_reset_pin(I2S_SD_PIN);
    gpio_set_direction(I2S_SD_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(I2S_SD_PIN, 1);

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
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

static void tembed_audio_task(void *pvParameters) {
    (void)pvParameters;
    tembed_i2s_init();

    tembed_sound_t cur_sound = TEMBED_SOUND_NONE;
    int16_t stereo_buf[128 * 2];

    while (1) {
        tembed_sound_t req;
        if (xQueueReceive(audio_queue, &req, portMAX_DELAY)) {
            cur_sound = req;
        }

        if (cur_sound == TEMBED_SOUND_NONE || cur_sound == TEMBED_SOUND_STOP) {
            continue;
        }

        const uint8_t *pcm_u8 = NULL;
        size_t total_samples = 0;
        bool loop = false;

        switch (cur_sound) {
            case TEMBED_SOUND_WAITING:
                pcm_u8 = sound_waiting_pcm;
                total_samples = sound_waiting_samples;
                loop = true;
                break;
            case TEMBED_SOUND_CONFIRMED:
                pcm_u8 = sound_confirmed_pcm;
                total_samples = sound_confirmed_samples;
                loop = false;
                break;
            case TEMBED_SOUND_TIMEOUT:
                pcm_u8 = sound_timeout_pcm;
                total_samples = sound_timeout_samples;
                loop = false;
                break;
            default:
                break;
        }

        if (!pcm_u8 || total_samples == 0) {
            cur_sound = TEMBED_SOUND_NONE;
            continue;
        }

        size_t pos = 0;
        while (pos < total_samples && cur_sound != TEMBED_SOUND_STOP && cur_sound != TEMBED_SOUND_NONE) {
            if (xQueueReceive(audio_queue, &req, 0)) {
                cur_sound = req;
                break;
            }

            size_t chunk = 128;
            if (pos + chunk > total_samples) {
                chunk = total_samples - pos;
            }

            for (size_t i = 0; i < chunk; i++) {
                int16_t s16 = ((int16_t)pcm_u8[pos + i] - 128) << 8;
                s16 = (int16_t)((int32_t)s16 * 7 / 10);
                stereo_buf[i * 2]     = s16;
                stereo_buf[i * 2 + 1] = s16;
            }

            tembed_i2s_write(stereo_buf, chunk * 2 * sizeof(int16_t));
            pos += chunk;

            if (pos >= total_samples && loop) {
                pos = 0;
                tembed_i2s_silence(256);
                for (int d = 0; d < 8; d++) {
                    if (xQueueReceive(audio_queue, &req, pdMS_TO_TICKS(100))) {
                        cur_sound = req;
                        break;
                    }
                }
            }
        }

        if (!loop || cur_sound != TEMBED_SOUND_WAITING) {
            cur_sound = TEMBED_SOUND_NONE;
            tembed_i2s_silence(128);
        }
    }
}

void tembed_audio_play(tembed_sound_t sound) {
    if (audio_queue) {
        xQueueSend(audio_queue, &sound, 0);
    }
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
        case SIGNAL_BOOT:
        case SIGNAL_USB_MOUNTED:
            tembed_audio_play(TEMBED_SOUND_STOP);
            break;
        default:
            break;
    }
    return 0;
}

void tembed_audio_start(void) {
    audio_queue = xQueueCreate(8, sizeof(tembed_sound_t));
    xTaskCreate(tembed_audio_task, "tembed_audio", 3072, NULL, 2, NULL);

    signal_add(SIGNAL_USER_PRESENCE_REQUEST, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_COMPLETED, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_TIMEOUT, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_USER_PRESENCE_CANCELLED, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_BOOT, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
    signal_add(SIGNAL_USB_MOUNTED, SIGNAL_FLAG_NONE, tembed_audio_signal_cb);
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
        print("main.c already patched with audio.")
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

    // 5. Enable MAX98357A Audio Amplifier Shutdown Pin (GPIO 46)
    gpio_reset_pin(GPIO_NUM_46);
    gpio_set_direction(GPIO_NUM_46, GPIO_MODE_OUTPUT);
    gpio_set_level(GPIO_NUM_46, 1);"""
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
