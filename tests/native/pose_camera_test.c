/* Actual app-service lease bridge, mocked hardware and legacy service body. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "FreeRTOS.h"
#include "semphr.h"
#include "rice_pose_camera.h"
static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #x); abort(); } } while (0)
typedef struct { bool ready; unsigned id; } lisa_device_t;
static lisa_device_t camera = {true, 1}, dvp = {true, 2}, gpio = {true, 3};
static struct { bool inited, streaming, suspended; lisa_device_t *camera_dev, *dvp_dev; } cam_ctx;
static struct {
    unsigned critical, mutex_created, init, resume, capture, stop, suspend,
        dvp_stop, destroy, reinit, buffers, poweroff, clockoff, disable, enable;
    bool fail_mutex, fail_init, fail_resume, fail_stop, fail_suspend,
        fail_reinit, fail_gpio, fail_clock, race_init, race_capture;
} m;
struct mock_semaphore { bool held; };
void mock_enter(void) { ++m.critical; }
void mock_exit(void) { CHECK(m.critical); --m.critical; }
SemaphoreHandle_t xSemaphoreCreateMutexStatic(StaticSemaphore_t *storage)
{
    CHECK(!m.critical && storage && sizeof(struct mock_semaphore) <= sizeof(*storage));
    ++m.mutex_created; if (m.fail_mutex) return NULL;
    SemaphoreHandle_t semaphore = (SemaphoreHandle_t)storage;
    semaphore->held = false; return semaphore;
}
BaseType_t xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t timeout)
{ (void)timeout; CHECK(!m.critical && mutex); if (mutex->held) return 0; mutex->held = true; return pdTRUE; }
BaseType_t xSemaphoreGive(SemaphoreHandle_t mutex)
{ CHECK(!m.critical && mutex->held); mutex->held = false; return pdTRUE; }
static void service_camera_context_reset(void) { memset(&cam_ctx, 0, sizeof(cam_ctx)); }
static int rice_camera_impl_init(void)
{
    CHECK(!m.critical); ++m.init;
    if (m.race_init) CHECK(service_camera_pose_claim() == RICE_CAMERA_BUSY);
    if (cam_ctx.inited) return 0;
    cam_ctx.camera_dev = &camera; cam_ctx.dvp_dev = &dvp; m.buffers += 2;
    if (m.fail_init) return -4;
    cam_ctx.inited = true; return 0;
}
int service_camera_init(void);
int service_camera_suspend(void);
static int rice_camera_impl_capture(uint8_t *bytes, uint32_t count)
{
    CHECK(bytes && count && !m.critical); ++m.capture; cam_ctx.streaming = true;
    if (m.race_capture) {
        CHECK(service_camera_init() == RICE_CAMERA_BUSY);
        CHECK(service_camera_suspend() == RICE_CAMERA_BUSY);
        CHECK(service_camera_pose_claim() == RICE_CAMERA_BUSY);
    }
    return 0;
}
static int rice_camera_impl_stop(void) { ++m.stop; cam_ctx.streaming = false; return 0; }
static int rice_camera_impl_suspend(void)
{ ++m.suspend; cam_ctx.suspended = true; ++m.poweroff; ++m.clockoff; m.disable += 2; return m.fail_suspend ? -1 : 0; }
static int rice_camera_impl_resume(void)
{ ++m.resume; return m.fail_resume ? -1 : 0; }
static int rice_camera_impl_get_framesize(uint16_t *w, uint16_t *h)
{ if (!cam_ctx.inited) return -1; *w = 320; *h = 240; return 0; }
static bool rice_camera_impl_is_inited(void) { return cam_ctx.inited; }
int lisa_dvp_stop(lisa_device_t *device)
{ CHECK(!m.critical && device == &dvp); ++m.dvp_stop; return m.fail_stop ? -1 : 0; }
static void lisa_device_destroy(lisa_device_t *device)
{ CHECK(!m.critical && device == &camera); ++m.destroy; device->ready = false; m.buffers = 0; }
static bool lisa_device_ready(lisa_device_t *device) { return device->ready; }
static int lisa_device_reinit(lisa_device_t *device)
{ CHECK(device == &camera); ++m.reinit; device->ready = !m.fail_reinit; return m.fail_reinit ? -1 : 0; }
static lisa_device_t *lisa_device_get(const char *name) { CHECK(!strcmp(name, "gpio")); return &gpio; }
#define CAM_PWDN_DEVICE_NAME "gpio"
#define CAM_PWDN_PIN 3
#define CAM_PWDN_ACTIVE_LEVEL 1
#define LISA_GPIO_HIGH 1
#define LISA_GPIO_LOW 0
static int lisa_gpio_write_pin(lisa_device_t *device, unsigned pin, unsigned level)
{ CHECK(device == &gpio && pin == 3 && level == 1); ++m.poweroff; return m.fail_gpio ? -1 : 0; }
#define DVP0() (&dvp)
static int DVP_DisableClockout(void *device) { CHECK(device == &dvp); ++m.clockoff; return m.fail_clock ? -1 : 0; }
#define __HAL_CRM_DVP_CLK_DISABLE() (++m.disable)
#define __HAL_CRM_VIDEO_CLK_DISABLE() (++m.disable)
#define __HAL_CRM_DVP_CLK_ENABLE() (++m.enable)
#define __HAL_CRM_VIDEO_CLK_ENABLE() (++m.enable)
#include "../../firmware/standalone/rice_pose_camera.inc"

static void reset(void)
{
    CHECK(!m.critical);
    if (rice_camera_mutex) CHECK(!rice_camera_mutex->held);
    rice_camera_mutex = NULL;
    rice_camera_mutex_creating = rice_camera_leased = rice_camera_poisoned = rice_camera_attempted = false;
    memset(&m, 0, sizeof(m)); service_camera_context_reset(); camera.ready = dvp.ready = gpio.ready = true;
}
int main(void)
{
    reset(); CHECK(service_camera_pose_init() == RICE_CAMERA_NOT_OWNER);
    CHECK(!m.init && !m.enable); CHECK(service_camera_pose_claim() == 0);
    CHECK(!m.init && !m.enable && !m.buffers); CHECK(service_camera_pose_claim() == RICE_CAMERA_BUSY);
    CHECK(service_camera_init() == RICE_CAMERA_BUSY); CHECK(!service_camera_is_inited());
    uint8_t bytes[2]; uint16_t width, height;
    CHECK(service_camera_capture(bytes, 2) == RICE_CAMERA_BUSY);
    CHECK(service_camera_stop() == RICE_CAMERA_BUSY); CHECK(service_camera_resume() == RICE_CAMERA_BUSY);
    CHECK(service_camera_suspend() == RICE_CAMERA_BUSY);
    CHECK(service_camera_get_framesize(&width, &height) == RICE_CAMERA_BUSY);
    CHECK(service_camera_pose_release() == 0); CHECK(!m.destroy && !m.poweroff);
    CHECK(service_camera_pose_release() == RICE_CAMERA_NOT_OWNER);
    reset(); m.race_init = true; CHECK(service_camera_init() == 0);
    CHECK(service_camera_is_inited()); CHECK(service_camera_pose_claim() == RICE_CAMERA_BUSY);
    CHECK(service_camera_suspend() == 0); CHECK(service_camera_pose_claim() == RICE_CAMERA_BUSY);
    reset(); m.race_capture = true;
    for (unsigned i = 0; i < 3; ++i) {
        CHECK(service_camera_pose_claim() == 0); CHECK(service_camera_pose_init() == 0);
        CHECK(m.buffers == 2); CHECK(service_camera_pose_get_framesize(&width, &height) == 0);
        CHECK(width == 320 && height == 240); CHECK(service_camera_pose_capture(bytes, 2) == 0);
        CHECK(service_camera_pose_release() == 0);
        CHECK(!m.buffers && !cam_ctx.inited && camera.ready && !rice_camera_leased);
        CHECK(m.poweroff == i + 1 && m.clockoff == i + 1); /* no access after clock-off */
    }
    CHECK(m.destroy == 3 && m.reinit == 3 && m.dvp_stop == 3);
    reset(); m.fail_init = true; CHECK(service_camera_pose_claim() == 0);
    CHECK(service_camera_pose_init() == -4 && m.buffers == 2 && !cam_ctx.inited);
    CHECK(service_camera_pose_release() == 0);
    CHECK(!m.buffers && m.destroy == 1 && m.poweroff == 1 && m.clockoff == 1);
    m.fail_init = false; CHECK(service_camera_pose_claim() == 0); CHECK(service_camera_pose_init() == 0);
    CHECK(service_camera_pose_release() == 0); CHECK(!m.buffers);
    reset(); m.fail_init = true; CHECK(service_camera_init() == -4); CHECK(!m.buffers && m.destroy == 1);
    reset(); CHECK(service_camera_init() == 0); m.fail_resume = true;
    CHECK(service_camera_resume() == -1); CHECK(!m.buffers && m.destroy == 1);
    reset(); m.fail_stop = true; CHECK(service_camera_pose_claim() == 0); CHECK(service_camera_pose_init() == 0);
    CHECK(service_camera_pose_release() == RICE_CAMERA_CLEANUP_FAILED);
    CHECK(m.buffers == 2 && m.destroy == 0); /* DMA may still own these: do not free. */
    CHECK(service_camera_pose_claim() == RICE_CAMERA_QUARANTINED);
    CHECK(service_camera_capture(bytes, 2) == RICE_CAMERA_QUARANTINED);
    for (unsigned failure = 0; failure < 4; ++failure) {
        reset(); CHECK(service_camera_pose_claim() == 0);
        if (failure < 2) m.fail_init = true;
        (void)service_camera_pose_init();
        m.fail_gpio = failure == 0; m.fail_clock = failure == 1;
        m.fail_suspend = failure == 2; m.fail_reinit = failure == 3;
        CHECK(service_camera_pose_release() == RICE_CAMERA_CLEANUP_FAILED);
        CHECK(!m.buffers && service_camera_pose_claim() == RICE_CAMERA_QUARANTINED);
    }
    reset(); m.fail_mutex = true; CHECK(service_camera_pose_claim() == RICE_CAMERA_BUSY);
    m.fail_mutex = false; CHECK(service_camera_pose_claim() == 0); CHECK(service_camera_pose_release() == 0);
    reset(); printf("pose camera bridge: %u checks passed (mock service/driver; no physical DMA).\n", checks);
    return 0;
}
