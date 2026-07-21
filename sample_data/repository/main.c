#include <stdio.h>
#include "wifi_config.h"

int main(void) {
    wifi_profile_t profile = {24, 165, "DemoSSID"};
    return apply_wifi_profile(&profile);
}
