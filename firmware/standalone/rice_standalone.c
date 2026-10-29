#include "rice_standalone.h"
#include "miniapp.h"
#include "miniapp_bundle.h"
#include "FreeRTOS.h"
#include "task.h"
#include "lisa_log.h"

static TickType_t s_last_attempt;
static unsigned s_attempts;
static bool s_started_once;

int rice_standalone_start(void)
{
    char error[128] = {0};
    s_last_attempt = xTaskGetTickCount();
    ++s_attempts;
    if (!miniapp_install_begin()) return -1;
    const miniapp_package_t package = {
        .id = RICE_APP_ID,
        .version = RICE_BUNDLE_VERSION,
        .hash = RICE_BUNDLE_MD5,
        .size = RICE_BUNDLE_SIZE,
    };
    int result = miniapp_install(&package, (const char *)rice_miniapp_source, error, sizeof(error));
    miniapp_install_end();
    if (result != 0) LISA_LOGE("ricestep", "builtin app failed: %s", error);
    else {
        s_started_once = true;
        LISA_LOGI("ricestep", "builtin app ready, no network required");
    }
    return result;
}

void rice_standalone_poll(void)
{
    /* Once boot succeeds, do not respawn after a manual hold/exit or a fault.
     * Preserve the stock exit-then-long-hold shutdown path. Cold boot reopens us. */
    if (s_started_once || miniapp_is_active()) return;
    /* A boot failure receives at most three attempts, never a busy loop. */
    if (s_attempts < 3 && xTaskGetTickCount() - s_last_attempt >= pdMS_TO_TICKS(2000)) {
        (void)rice_standalone_start();
    }
}
