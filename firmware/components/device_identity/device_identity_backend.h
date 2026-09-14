#pragma once

#include "device_identity.h"

esp_err_t device_identity_esp_provider(device_identity_provider_t *provider);
void device_identity_record_failure(device_identity_failure_stage_t stage,
                                    int32_t detail);
