/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "dby-chg-core.h"
int main(void)
{
	int t, prev = INT_MAX;
	double worst = 0;
	assert(!dby_batt_therm_decic(468750, 1875000, &t) && t == 250);
	int values[] = {INT_MIN, -1, 0, 1875000, INT_MAX};
	for (unsigned i=0; i<sizeof(values)/sizeof(values[0]); i++) {
		t=12345; assert(dby_batt_therm_decic(values[i],1875000,&t)<0); assert(t==12345);
	}
	assert(dby_batt_therm_decic(1,0,&t)==-EINVAL);
	assert(dby_batt_therm_decic(1,10000001,&t)==-EINVAL);
	assert(dby_batt_therm_decic(1,1875000,NULL)==-EINVAL);
	assert(dby_batt_therm_decic(1,1875000,&t)==-ERANGE);
	assert(dby_batt_therm_decic(1874999,1875000,&t)==-ERANGE);
	puts("PASS 25 C point; rails, near rails, invalid reference/null; error leaves output unchanged");
	for (int uv=1;uv<1875000;uv++) {
		if (dby_batt_therm_decic(uv,1875000,&t)) continue;
		assert(t<=prev); prev=t;
		double r=30000.0*uv/(1875000-uv);
		double expected=1.0/(1.0/298.15+log(r/10000.0)/3435.0)-273.15;
		double e=fabs(t/10.0-expected); if(e>worst)worst=e;
		assert(e<0.06);
	}
	printf("PASS exhaustive 1..1874999 uV monotonicity; max model error %.6f C (<0.06 C)\n",worst);
	for (int ref=1000000;ref<=2500000;ref+=125000)
		for(int decic=-400;decic<=1000;decic++) {
			double r=10000.0*exp(3435.0*(1.0/(decic/10.0+273.15)-1.0/298.15));
			int uv=(int)llround(ref*r/(30000.0+r));
			assert(!dby_batt_therm_decic(uv,ref,&t));
			assert(abs(t-decic)<=1);
		}
	puts("PASS beta inverse round-trip -40..100 C at 13 reference voltages (<=0.1 C)");
	return 0;
}
