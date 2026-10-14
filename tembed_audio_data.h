#ifndef _TEMBED_AUDIO_DATA_H_
#define _TEMBED_AUDIO_DATA_H_

#include <stdint.h>
#include <stddef.h>

/* Default placeholder - will be generated with Kamen Rider audio in CI build */
static const uint8_t sound_waiting_pcm[1] = {128};
#define sound_waiting_samples 0

static const uint8_t sound_confirmed_pcm[1] = {128};
#define sound_confirmed_samples 0

static const uint8_t sound_timeout_pcm[1] = {128};
#define sound_timeout_samples 0

#endif // _TEMBED_AUDIO_DATA_H_
