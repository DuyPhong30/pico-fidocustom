/*
 * ZW111 Fingerprint Sensor Driver for LILYGO T-Embed (ESP32-S3)
 * Port pins: TX = GPIO 43, RX = GPIO 44 (UART_NUM_1 @ 57600 baud)
 */

#include "zw111.h"
#include <string.h>
#include <stdio.h>

#ifdef ESP_PLATFORM
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "ZW111";

#define ZW111_UART_NUM      UART_NUM_1
#define ZW111_TX_PIN        43
#define ZW111_RX_PIN        44
#define ZW111_BAUD_RATE     57600
#define ZW111_BUF_SIZE      512

static bool s_initialized = false;
static bool s_connected = false;
static uint16_t s_template_count = 0;

static uint16_t zw111_calc_checksum(uint8_t pid, const uint8_t *payload, size_t payload_len) {
    uint16_t length = payload_len + 2;
    uint32_t total = pid + (length >> 8) + (length & 0xFF);
    for (size_t i = 0; i < payload_len; i++) {
        total += payload[i];
    }
    return (uint16_t)total;
}

static bool zw111_send_command(uint8_t instruction, const uint8_t *params, size_t param_len,
                               uint8_t *confirm, uint8_t *data, size_t *data_len,
                               uint32_t timeout_ms) {
    if (!s_initialized) {
        return false;
    }

    // Drain any leftover bytes in the UART RX buffer
    uint8_t drain[64];
    while (uart_read_bytes(ZW111_UART_NUM, drain, sizeof(drain), 0) > 0) {}

    uint8_t payload[32];
    if (param_len + 1 > sizeof(payload)) return false;
    payload[0] = instruction;
    if (param_len > 0 && params != NULL) {
        memcpy(payload + 1, params, param_len);
    }

    const size_t payload_len = param_len + 1;
    const uint16_t length = payload_len + 2;
    const uint16_t sum = zw111_calc_checksum(0x01, payload, payload_len);

    const uint8_t header[] = {
        0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x01,
        (uint8_t)(length >> 8), (uint8_t)(length & 0xFF)
    };

    if (uart_write_bytes(ZW111_UART_NUM, (const char *)header, sizeof(header)) != sizeof(header) ||
        uart_write_bytes(ZW111_UART_NUM, (const char *)payload, payload_len) != (int)payload_len) {
        s_connected = false;
        return false;
    }

    uint8_t sum_bytes[] = { (uint8_t)(sum >> 8), (uint8_t)(sum & 0xFF) };
    if (uart_write_bytes(ZW111_UART_NUM, (const char *)sum_bytes, sizeof(sum_bytes)) != sizeof(sum_bytes)) {
        s_connected = false;
        return false;
    }

    uint8_t response[96];
    size_t pos = 0;
    const size_t data_cap = (data && data_len) ? *data_len : 0;
    size_t out_len = 0;
    bool saw_ack = false;
    TickType_t start = xTaskGetTickCount();
    TickType_t deadline = pdMS_TO_TICKS(timeout_ms);
    if (data && data_len) *data_len = 0;

    while ((xTaskGetTickCount() - start) < deadline) {
        while (true) {
            // Find 0xEF, 0x01 preamble
            while (pos >= 2 && !(response[0] == 0xEF && response[1] == 0x01)) {
                memmove(response, response + 1, --pos);
            }
            if (pos < 9) break;

            uint8_t pid = response[6];
            uint16_t resp_len = ((uint16_t)response[7] << 8) | response[8];
            size_t expected = 9 + resp_len;

            if (response[2] != 0xFF || response[3] != 0xFF ||
                response[4] != 0xFF || response[5] != 0xFF || resp_len < 2) {
                s_connected = false;
                return false;
            }
            if (expected > sizeof(response)) {
                s_connected = false;
                return false;
            }
            if (pos < expected) break;

            size_t response_payload_len = resp_len - 2;

            // Verify checksum
            uint16_t expected_sum = zw111_calc_checksum(pid, response + 9, response_payload_len);
            uint16_t received_sum = ((uint16_t)response[expected - 2] << 8) | response[expected - 1];
            if (expected_sum != received_sum) {
                ESP_LOGW(TAG, "Checksum mismatch (exp 0x%04x, got 0x%04x)", expected_sum, received_sum);
                s_connected = false;
                return false;
            }

            if (pid == 0x07) { // ACK packet
                if (response_payload_len < 1) {
                    s_connected = false;
                    return false;
                }
                s_connected = true;
                if (confirm) *confirm = response[9];
                saw_ack = true;

                size_t actual_len = response_payload_len - 1;
                if (data && data_len && actual_len) {
                    size_t copy_len = (actual_len > data_cap - out_len) ? (data_cap - out_len) : actual_len;
                    memcpy(data + out_len, response + 10, copy_len);
                    out_len += copy_len;
                    *data_len = out_len;
                }
                return true;
            }

            size_t remaining = pos - expected;
            if (remaining) memmove(response, response + expected, remaining);
            pos = remaining;
        }

        int n = uart_read_bytes(ZW111_UART_NUM, response + pos, 1, pdMS_TO_TICKS(10));
        if (n > 0) {
            pos += (size_t)n;
            n = uart_read_bytes(ZW111_UART_NUM, response + pos, sizeof(response) - pos, 0);
            if (n > 0) pos += (size_t)n;
        }
    }

    return saw_ack;
}

extern bool req_button_pending;
static TaskHandle_t s_knob_task_handle = NULL;
static int s_current_tx = 43;
static int s_current_rx = 44;
static int s_current_baud = 57600;

bool zw111_probe(void) {
    if (!s_initialized) {
        uart_config_t uart_cfg = {
            .baud_rate = 57600,
            .data_bits = UART_DATA_8_BITS,
            .parity = UART_PARITY_DISABLE,
            .stop_bits = UART_STOP_BITS_1,
            .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
            .source_clk = UART_SCLK_DEFAULT,
        };

        esp_err_t err = uart_driver_install(ZW111_UART_NUM, 1024, 0, 0, NULL, 0);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGE(TAG, "uart_driver_install failed: %d", err);
            return false;
        }

        uart_param_config(ZW111_UART_NUM, &uart_cfg);
        uart_set_rx_timeout(ZW111_UART_NUM, 2);
        s_initialized = true;
    }

    const int pin_pairs[][2] = {
        { 43, 44 },  // Mode A: ESP32 TX=43, RX=44
        { 44, 43 }   // Mode B: ESP32 TX=44, RX=43 (swapped)
    };
    const int bauds[] = { 57600, 115200 };

    for (int p = 0; p < 2; p++) {
        int tx = pin_pairs[p][0];
        int rx = pin_pairs[p][1];

        for (int b = 0; b < 2; b++) {
            int baud = bauds[b];

            uart_set_baudrate(ZW111_UART_NUM, baud);
            uart_set_pin(ZW111_UART_NUM, tx, rx, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
            vTaskDelay(pdMS_TO_TICKS(30));

            // Drain RX buffer
            uint8_t drain[64];
            while (uart_read_bytes(ZW111_UART_NUM, drain, sizeof(drain), 0) > 0) {}

            // Send PS_GetImage (0x01) probe
            uint8_t confirm = 0xFF;
            if (zw111_send_command(0x01, NULL, 0, &confirm, NULL, NULL, 120)) {
                // SENSOR RESPONDED! Communication working!
                s_connected = true;
                s_current_tx = tx;
                s_current_rx = rx;
                s_current_baud = baud;
                ESP_LOGI(TAG, "ZW111 MATCHED! TX=%d, RX=%d, BAUD=%d (confirm=0x%02x)", tx, rx, baud, confirm);

                zw111_refresh_template_count();
                // Turn OFF default blue light to confirm handshake!
                zw111_set_led(4, 0, 0, 0);
                return true;
            }
        }
    }
    return false;
}

static inline bool is_user_button_pressed(void) {
    return (gpio_get_level(GPIO_NUM_0) == 0) || (gpio_get_level(GPIO_NUM_6) == 0);
}

static void zw111_knob_task(void *pvParameters) {
    (void)pvParameters;
    uint32_t click_count = 0;
    uint32_t last_click_time = 0;
    bool last_state = false;
    uint32_t last_probe_time = 0;

    while (1) {
        uint32_t now = pdTICKS_TO_MS(xTaskGetTickCount());

        // In idle, if sensor not yet connected, auto-probe every 2 seconds
        if (!req_button_pending && !s_connected && (now - last_probe_time > 2000)) {
            last_probe_time = now;
            zw111_probe();
        }

        // Only monitor when in IDLE
        if (!req_button_pending) {
            bool current_state = is_user_button_pressed();

            // Detect button press edge: unpressed -> pressed
            if (current_state && !last_state) {
                TickType_t press_start = xTaskGetTickCount();
                bool long_press_handled = false;

                // Loop while held down
                while (is_user_button_pressed()) {
                    vTaskDelay(pdMS_TO_TICKS(40));
                    uint32_t held_ms = pdTICKS_TO_MS(xTaskGetTickCount() - press_start);

                    // If held for 10 seconds: Delete all templates!
                    if (held_ms >= 10000) {
                        zw111_delete_all();
                        long_press_handled = true;
                        click_count = 0;
                        while (is_user_button_pressed()) {
                            vTaskDelay(pdMS_TO_TICKS(40));
                        }
                        break;
                    }
                }

                if (!long_press_handled) {
                    uint32_t total_held_ms = pdTICKS_TO_MS(xTaskGetTickCount() - press_start);

                    if (total_held_ms >= 2000) {
                        // Long press >= 2s: Trigger enrollment!
                        click_count = 0;
                        ESP_LOGI(TAG, "Knob long press detected -> starting enrollment");
                        if (!s_connected) zw111_probe();
                        zw111_enroll_finger();
                    } else if (total_held_ms >= 20) {
                        // Valid click (> 20ms debounce, < 2s)
                        if (now - last_click_time < 750) {
                            click_count++;
                        } else {
                            click_count = 1;
                        }
                        last_click_time = now;
                        ESP_LOGI(TAG, "Knob click #%u", click_count);

                        if (click_count >= 3) {
                            click_count = 0;
                            // TRIPLE CLICK DETECTED!
                            ESP_LOGI(TAG, "Knob TRIPLE-CLICK detected -> starting enrollment!");
                            if (!s_connected) zw111_probe();
                            zw111_enroll_finger();
                        }
                    }
                }
            }
            last_state = current_state;

            // Reset click count if timeout (> 800ms since last click)
            if (click_count > 0 && (now - last_click_time > 800)) {
                click_count = 0;
            }
        } else {
            click_count = 0;
            last_state = false;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

bool zw111_init(void) {
    // Configure buttons: GPIO 0 (Rotary knob) and GPIO 6 (Side button)
    gpio_config_t btn_cfg = {
        .pin_bit_mask = (1ULL << GPIO_NUM_0) | (1ULL << GPIO_NUM_6),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&btn_cfg);
    if (s_initialized && s_connected) {
        return true;
    }

    // Try auto-probe
    zw111_probe();

    // Always create knob task to monitor clicks and holds
    if (s_knob_task_handle == NULL) {
        xTaskCreate(zw111_knob_task, "zw_knob", 3072, NULL, 3, &s_knob_task_handle);
    }

    return s_connected;
}

bool zw111_is_connected(void) {
    return s_connected;
}

uint16_t zw111_get_template_count(void) {
    return s_template_count;
}

uint16_t zw111_refresh_template_count(void) {
    if (!s_initialized && !zw111_init()) {
        return 0;
    }

    uint8_t confirm = 0xFF;
    uint8_t count_data[4];
    size_t count_len = sizeof(count_data);

    if (zw111_send_command(0x1D, NULL, 0, &confirm, count_data, &count_len, 200) && confirm == 0x00) {
        if (count_len >= 2) {
            s_template_count = ((uint16_t)count_data[0] << 8) | count_data[1];
            ESP_LOGI(TAG, "ZW111 Enrolled Templates: %u", s_template_count);
            return s_template_count;
        }
    }
    return s_template_count;
}

bool zw111_set_led(uint8_t effect, uint8_t start_color, uint8_t end_color, uint8_t cycles) {
    if (!s_initialized && !zw111_init()) {
        return false;
    }

    // PS_ControlBLN (0x3c)
    uint8_t params[4] = { effect, start_color, end_color, cycles };
    uint8_t confirm = 0xFF;
    return zw111_send_command(0x3C, params, sizeof(params), &confirm, NULL, NULL, 150) && (confirm == 0x00);
}

zw111_status_t zw111_check_presence(void) {
    if (!s_initialized && !zw111_init()) {
        return ZW111_STATE_ERROR;
    }

    // Send PS_GetImage (0x01)
    uint8_t confirm = 0xFF;
    bool replied = zw111_send_command(0x01, NULL, 0, &confirm, NULL, NULL, 60);

    if (!replied) {
        return ZW111_STATE_ERROR;
    }

    if (confirm == 0x02) {
        // No finger on sensor
        return ZW111_STATE_NO_FINGER;
    }

    if (confirm == 0x00) {
        // Finger placed and image acquired!
        if (s_template_count > 0) {
            // Generate character file into buffer 1
            uint8_t gen_params[1] = { 0x01 };
            uint8_t gen_confirm = 0xFF;
            if (zw111_send_command(0x02, gen_params, 1, &gen_confirm, NULL, NULL, 150) && gen_confirm == 0x00) {
                // Search template library (buffer 1, start 0, count 256)
                uint8_t search_params[5] = { 0x01, 0x00, 0x00, 0x01, 0x00 };
                uint8_t search_data[4];
                size_t search_len = sizeof(search_data);
                uint8_t search_confirm = 0xFF;
                if (zw111_send_command(0x04, search_params, 5, &search_confirm, search_data, &search_len, 250) && search_confirm == 0x00) {
                    uint16_t slot = ((uint16_t)search_data[0] << 8) | search_data[1];
                    uint16_t score = ((uint16_t)search_data[2] << 8) | search_data[3];
                    ESP_LOGI(TAG, "Biometric MATCH: slot=%u score=%u", slot, score);
                    return ZW111_STATE_MATCH_CONFIRMED;
                }
            }
            ESP_LOGW(TAG, "Biometric MISMATCH (unrecognized finger)");
            return ZW111_STATE_MISMATCH;
        } else {
            // No templates enrolled: Any valid finger touch fulfills User Presence!
            ESP_LOGI(TAG, "User Presence Touch CONFIRMED (no templates enrolled)");
            return ZW111_STATE_TOUCH_CONFIRMED;
        }
    }

    return ZW111_STATE_NO_FINGER;
}

int zw111_enroll_finger(void) {
    if (!s_initialized && !zw111_init()) {
        return -1;
    }

    ESP_LOGI(TAG, "Starting Fingerprint Enrollment...");

    // Step 1: Steady Purple - Prompt user to place finger
    zw111_set_led(3, 5, 5, 0);

    TickType_t start = xTaskGetTickCount();
    uint8_t confirm = 0xFF;
    bool got_first = false;

    // Wait up to 10s for first touch
    while ((xTaskGetTickCount() - start) < pdMS_TO_TICKS(10000)) {
        if (zw111_send_command(0x01, NULL, 0, &confirm, NULL, NULL, 80) && confirm == 0x00) {
            uint8_t p1[1] = { 0x01 };
            if (zw111_send_command(0x02, p1, 1, &confirm, NULL, NULL, 200) && confirm == 0x00) {
                got_first = true;
                break;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }

    if (!got_first) {
        zw111_set_led(2, 1, 1, 3); // Flash Red 3 times
        return -1;
    }

    // Step 2: Flash Blue - Prompt user to lift finger
    zw111_set_led(2, 4, 4, 3);
    vTaskDelay(pdMS_TO_TICKS(600));

    // Wait for finger to lift
    start = xTaskGetTickCount();
    while ((xTaskGetTickCount() - start) < pdMS_TO_TICKS(5000)) {
        if (zw111_send_command(0x01, NULL, 0, &confirm, NULL, NULL, 80) && confirm == 0x02) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }

    // Step 3: Steady Cyan - Prompt user to place same finger again
    zw111_set_led(3, 6, 6, 0);
    start = xTaskGetTickCount();
    bool got_second = false;

    while ((xTaskGetTickCount() - start) < pdMS_TO_TICKS(10000)) {
        if (zw111_send_command(0x01, NULL, 0, &confirm, NULL, NULL, 80) && confirm == 0x00) {
            uint8_t p2[1] = { 0x02 };
            if (zw111_send_command(0x02, p2, 1, &confirm, NULL, NULL, 200) && confirm == 0x00) {
                got_second = true;
                break;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }

    if (!got_second) {
        zw111_set_led(2, 1, 1, 3); // Flash Red 3 times
        return -1;
    }

    // Step 4: Combine model (PS_RegModel 0x05)
    if (!zw111_send_command(0x05, NULL, 0, &confirm, NULL, NULL, 300) || confirm != 0x00) {
        ESP_LOGW(TAG, "RegModel failed (confirm=0x%02x)", confirm);
        zw111_set_led(2, 1, 1, 3); // Flash Red
        return -1;
    }

    // Step 5: Store template (PS_StoreChar 0x06) into next slot
    uint16_t next_slot = s_template_count;
    uint8_t store_params[3] = { 0x01, (uint8_t)(next_slot >> 8), (uint8_t)(next_slot & 0xFF) };
    if (!zw111_send_command(0x06, store_params, 3, &confirm, NULL, NULL, 300) || confirm != 0x00) {
        ESP_LOGW(TAG, "StoreChar failed (confirm=0x%02x)", confirm);
        zw111_set_led(2, 1, 1, 3);
        return -1;
    }

    s_template_count++;
    ESP_LOGI(TAG, "Fingerprint enrolled successfully to slot %u!", next_slot);

    // Flash Green 3 times for victory!
    zw111_set_led(2, 2, 2, 3);
    vTaskDelay(pdMS_TO_TICKS(800));
    zw111_set_led(4, 0, 0, 0);

    return (int)next_slot;
}

bool zw111_delete_all(void) {
    if (!s_initialized && !zw111_init()) {
        return false;
    }

    uint8_t confirm = 0xFF;
    // PS_Empty (0x0d)
    bool ok = zw111_send_command(0x0D, NULL, 0, &confirm, NULL, NULL, 500) && (confirm == 0x00);
    if (ok) {
        s_template_count = 0;
        ESP_LOGI(TAG, "All fingerprint templates deleted.");
        zw111_set_led(2, 1, 1, 2); // Flash Red 2 times
        vTaskDelay(pdMS_TO_TICKS(500));
        zw111_set_led(4, 0, 0, 0);
    }
    return ok;
}

#else

// Mock implementation for non-ESP platforms
bool zw111_init(void) { return false; }
bool zw111_is_connected(void) { return false; }
uint16_t zw111_get_template_count(void) { return 0; }
uint16_t zw111_refresh_template_count(void) { return 0; }
bool zw111_set_led(uint8_t e, uint8_t s, uint8_t d, uint8_t c) { return false; }
zw111_status_t zw111_check_presence(void) { return ZW111_STATE_NONE; }
int zw111_enroll_finger(void) { return -1; }
bool zw111_delete_all(void) { return false; }

#endif
