/*
 * ZW111 Fingerprint Sensor Driver for LILYGO T-Embed (ESP32-S3)
 * Port pins: TX = GPIO 43, RX = GPIO 44 (UART_NUM_1 @ 57600 baud)
 */

#ifndef _ZW111_H_
#define _ZW111_H_

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    ZW111_STATE_NONE = 0,
    ZW111_STATE_NO_FINGER,
    ZW111_STATE_TOUCH_CONFIRMED,   // Valid finger detected on sensor
    ZW111_STATE_MATCH_CONFIRMED,   // Finger matches enrolled biometric template
    ZW111_STATE_MISMATCH,          // Finger does not match enrolled template
    ZW111_STATE_ERROR              // Sensor not responding / packet error
} zw111_status_t;

// Initialize UART1 and verify connection with ZW111
bool zw111_init(void);

// Check if sensor is alive and communication is working
bool zw111_is_connected(void);

// Get number of enrolled templates in sensor memory
uint16_t zw111_get_template_count(void);

// Refresh template count from sensor
uint16_t zw111_refresh_template_count(void);

// Control sensor ring LED (PS_ControlBLN 0x3c)
// effect: 1=Breathing, 2=Flashing, 3=Steady ON, 4=Steady OFF
// colors: 1=Red, 2=Green, 4=Blue, 3=Yellow, 5=Purple, 6=Cyan, 7=White
bool zw111_set_led(uint8_t effect, uint8_t start_color, uint8_t end_color, uint8_t cycles);

// Poll sensor for finger presence / verification
zw111_status_t zw111_check_presence(void);

// Enroll current finger into next available slot (requires 2 touches)
// Returns slot number on success (>= 0), or -1 on failure
int zw111_enroll_finger(void);

// Delete all enrolled templates from sensor memory
bool zw111_delete_all(void);

#ifdef __cplusplus
}
#endif

#endif // _ZW111_H_
