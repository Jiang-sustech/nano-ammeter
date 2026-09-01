#include "adc_user.h"
#include "adc.h"

float ADC_Read_Voltage(void)
{
uint16_t adc;

HAL_ADC_Start(&hadc1);

HAL_ADC_PollForConversion(&hadc1,10);

adc=HAL_ADC_GetValue(&hadc1);

HAL_ADC_Stop(&hadc1);

return adc*3.3f/4095;

}