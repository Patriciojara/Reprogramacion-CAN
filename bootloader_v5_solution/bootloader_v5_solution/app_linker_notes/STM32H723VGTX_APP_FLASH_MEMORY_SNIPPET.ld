/* Reemplaza SOLO la región FLASH de la aplicación principal por esto.
 * La app NO debe partir en 0x08000000, debe partir en 0x08020000.
 */
MEMORY
{
  ITCMRAM (xrw)    : ORIGIN = 0x00000000,   LENGTH = 64K
  DTCMRAM (xrw)    : ORIGIN = 0x20000000,   LENGTH = 128K
  FLASH   (rx)     : ORIGIN = 0x08020000,   LENGTH = 892K
  RAM_D1  (xrw)    : ORIGIN = 0x24000000,   LENGTH = 320K
  RAM_D2  (xrw)    : ORIGIN = 0x30000000,   LENGTH = 32K
  RAM_D3  (xrw)    : ORIGIN = 0x38000000,   LENGTH = 16K
}
