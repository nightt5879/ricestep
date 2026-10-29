/* Appended to pinned service_camera.c, where the original API has been renamed.
 * The lease covers ALL service entrypoints, including legacy init/resume. No
 * replacement of the driver, ISR, DMA implementation or sensor register table. */
#include "rice_pose_camera.h"
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"
#include "lisa_dvp.h"
#if defined(CONFIG_ARCS_GPDMA_DATA_ONLY) && CONFIG_ARCS_GPDMA_DATA_ONLY
#error "Pose camera requires the standard reference-counted GPDMA HAL profile"
#endif

static SemaphoreHandle_t rice_camera_mutex;
static StaticSemaphore_t rice_camera_mutex_storage;
static bool rice_camera_mutex_creating, rice_camera_leased, rice_camera_poisoned;
static bool rice_camera_attempted;

static bool rice_camera_lock(TickType_t timeout)
{
    bool create = false;
    SemaphoreHandle_t mutex;
    taskENTER_CRITICAL();
    mutex = rice_camera_mutex;
    if (!mutex && !rice_camera_mutex_creating) {
        rice_camera_mutex_creating = true;
        create = true;
    }
    taskEXIT_CRITICAL();
    if (create) {
        /* Caller-owned storage avoids the SDK's fatal pvPortMalloc hook. */
        mutex = xSemaphoreCreateMutexStatic(&rice_camera_mutex_storage);
        taskENTER_CRITICAL();
        rice_camera_mutex = mutex;
        rice_camera_mutex_creating = false;
        taskEXIT_CRITICAL();
    }
    return mutex && xSemaphoreTake(mutex, timeout) == pdTRUE;
}

static void rice_camera_unlock(void) { (void)xSemaphoreGive(rice_camera_mutex); }

static int rice_camera_cleanup(void)
{
    if (!rice_camera_attempted) return 0;
    int failed = 0;
    bool was_inited = cam_ctx.inited;
    /* Stop the camera DMA channel before freeing its buffers. Do NOT destroy
     * dvp0: retain its boot-owned mutex/config rather than tearing down a
     * shared HAL unnecessarily. Standard HAL init/uninit is reference-counted;
     * the DATA_ONLY alternative above is not supported by this overlay. */
    if (cam_ctx.inited && cam_ctx.dvp_dev && lisa_dvp_stop(cam_ctx.dvp_dev) != 0) {
        rice_camera_poisoned = true;
        return RICE_CAMERA_CLEANUP_FAILED; /* Keep borrowed DMA buffers alive. */
    }
    if (cam_ctx.inited) {
        (void)rice_camera_impl_stop();
        if (rice_camera_impl_suspend() != 0) failed = 1;
    }
    /* setup can fail after allocating FBs, before cam_ctx.inited is set.
     * Device deinit is deliberately used instead of the service's no-op
     * suspend-on-uninitialized branch. It frees partial FBs and queues. */
    if (cam_ctx.camera_dev) {
        lisa_device_destroy(cam_ctx.camera_dev);
        if (lisa_device_ready(cam_ctx.camera_dev) ||
            lisa_device_reinit(cam_ctx.camera_dev) != 0) failed = 1;
    }
    /* The partial-init branch also needs PWDN/XCLK off. Normal suspend already
     * does this; repeat is safe and makes both paths equivalent. */
    if (!was_inited) {
        lisa_device_t *gpio = lisa_device_get(CAM_PWDN_DEVICE_NAME);
        if (!gpio || !lisa_device_ready(gpio) ||
            lisa_gpio_write_pin(gpio, CAM_PWDN_PIN,
                CAM_PWDN_ACTIVE_LEVEL ? LISA_GPIO_HIGH : LISA_GPIO_LOW) != 0) failed = 1;
        if (DVP_DisableClockout(DVP0()) != 0) failed = 1;
        __HAL_CRM_DVP_CLK_DISABLE();
        __HAL_CRM_VIDEO_CLK_DISABLE();
    }
    service_camera_context_reset();
    rice_camera_attempted = false;
    if (failed) rice_camera_poisoned = true;
    return failed ? RICE_CAMERA_CLEANUP_FAILED : 0;
}

int service_camera_pose_claim(void)
{
    if (!rice_camera_lock(0)) return RICE_CAMERA_BUSY;
    int result = rice_camera_poisoned ? RICE_CAMERA_QUARANTINED :
        (rice_camera_leased || cam_ctx.inited ? RICE_CAMERA_BUSY : 0);
    if (!result) rice_camera_leased = true;
    rice_camera_unlock();
    return result;
}

int service_camera_pose_init(void)
{
    if (!rice_camera_lock(0)) return RICE_CAMERA_BUSY;
    int result = RICE_CAMERA_NOT_OWNER;
    if (rice_camera_leased && !rice_camera_poisoned) {
        rice_camera_attempted = true;
        __HAL_CRM_VIDEO_CLK_ENABLE();
        __HAL_CRM_DVP_CLK_ENABLE();
        result = rice_camera_impl_init();
        if (!result) result = rice_camera_impl_resume();
        /* Release handles failure as well; no retries before cleanup. */
    }
    rice_camera_unlock();
    return result;
}

int service_camera_pose_capture(uint8_t *buffer, uint32_t bytes)
{
    if (!rice_camera_lock(0)) return RICE_CAMERA_BUSY;
    int result = rice_camera_leased && !rice_camera_poisoned ?
        rice_camera_impl_capture(buffer, bytes) : RICE_CAMERA_NOT_OWNER;
    rice_camera_unlock();
    return result;
}

int service_camera_pose_get_framesize(uint16_t *width, uint16_t *height)
{
    if (!rice_camera_lock(0)) return RICE_CAMERA_BUSY;
    int result = rice_camera_leased && !rice_camera_poisoned ?
        rice_camera_impl_get_framesize(width, height) : RICE_CAMERA_NOT_OWNER;
    rice_camera_unlock();
    return result;
}

int service_camera_pose_release(void)
{
    if (!rice_camera_lock(portMAX_DELAY)) return RICE_CAMERA_BUSY;
    int result = RICE_CAMERA_NOT_OWNER;
    if (rice_camera_leased) {
        result = rice_camera_cleanup();
        rice_camera_leased = false;
    }
    rice_camera_unlock();
    return result;
}

#define RICE_CAMERA_LEGACY_CALL(expression) do { \
    if (!rice_camera_lock(0)) return RICE_CAMERA_BUSY; \
    int result = rice_camera_leased ? RICE_CAMERA_BUSY : \
        (rice_camera_poisoned ? RICE_CAMERA_QUARANTINED : (expression)); \
    rice_camera_unlock(); return result; \
} while (0)

int service_camera_init(void)
{
    if (!rice_camera_lock(0)) return RICE_CAMERA_BUSY;
    int result = RICE_CAMERA_BUSY;
    if (!rice_camera_leased && !rice_camera_poisoned) {
        rice_camera_attempted = true;
        __HAL_CRM_VIDEO_CLK_ENABLE();
        __HAL_CRM_DVP_CLK_ENABLE();
        result = rice_camera_impl_init();
        if (result) (void)rice_camera_cleanup();
    }
    rice_camera_unlock();
    return result;
}
int service_camera_capture(uint8_t *buffer, uint32_t bytes)
{ RICE_CAMERA_LEGACY_CALL(rice_camera_impl_capture(buffer, bytes)); }
int service_camera_stop(void)
{ RICE_CAMERA_LEGACY_CALL(rice_camera_impl_stop()); }
int service_camera_suspend(void)
{ RICE_CAMERA_LEGACY_CALL(rice_camera_impl_suspend()); }
int service_camera_resume(void)
{
    if (!rice_camera_lock(0)) return RICE_CAMERA_BUSY;
    int result = RICE_CAMERA_BUSY;
    if (!rice_camera_leased && !rice_camera_poisoned) {
        rice_camera_attempted = true;
        result = rice_camera_impl_resume();
        if (result) (void)rice_camera_cleanup();
    }
    rice_camera_unlock();
    return result;
}
int service_camera_get_framesize(uint16_t *width, uint16_t *height)
{ RICE_CAMERA_LEGACY_CALL(rice_camera_impl_get_framesize(width, height)); }
bool service_camera_is_inited(void)
{
    if (!rice_camera_lock(0)) return false;
    bool result = !rice_camera_leased && !rice_camera_poisoned && rice_camera_impl_is_inited();
    rice_camera_unlock();
    return result;
}
#undef RICE_CAMERA_LEGACY_CALL
