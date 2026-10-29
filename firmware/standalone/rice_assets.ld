/* This ROM slot is included before the generic .rodata wildcard. It is not
 * registered with SCATLOAD and therefore is never copied into PSRAM/SRAM. */
.ricestep.assets : ALIGN(4) {
    __ricestep_assets_start = .;
    KEEP(*(.rodata.ricestep_voice))
    KEEP(*(.rodata.ricestep_app))
    . = ALIGN(4);
    __ricestep_assets_end = .;
} >ROM AT>ROM
ASSERT(ADDR(.ricestep.assets) >= ORIGIN(ROM), "RiceStep assets must be in ROM")
ASSERT(ADDR(.ricestep.assets) + SIZEOF(.ricestep.assets) <= ORIGIN(ROM) + LENGTH(ROM), "RiceStep assets exceed ROM")
