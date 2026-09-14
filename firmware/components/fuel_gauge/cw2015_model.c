#include "cw2015.h"

#include <stddef.h>

void cw2015_decode_measurement(const uint8_t vcell[2], const uint8_t soc[2],
                               fuel_gauge_sample_t *sample)
{
    if (vcell == NULL || soc == NULL || sample == NULL) {
        return;
    }
    const uint16_t raw_vcell = ((uint16_t)vcell[0] << 8) | vcell[1];
    float percent = (float)soc[0] + (float)soc[1] / 256.0f;
    if (percent > 100.0f) {
        percent = 100.0f;
    }
    sample->voltage_v = (float)raw_vcell * 0.000305f;
    sample->soc_percent = percent;
    sample->valid = true;
}
