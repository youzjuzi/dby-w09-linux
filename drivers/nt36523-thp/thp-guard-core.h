/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef THP_GUARD_CORE_H
#define THP_GUARD_CORE_H
/* Additive guard over TG26's unchanged tracking/confirm/filter. Constants are
 * an explicitly constrained best effort, not a claim that palm P1 was met. */
#define TG_N 2U
#define TG_R 1U
#define TG_M 4U
#define TG_L 8U
#define TG_WORDS (TM_WORDS+TM_MAX*3+TA_N)
struct tg_core {u32 age[TM_MAX],need[TM_MAX];bool reported[TM_MAX];u16 ttl[TA_N];};
static inline void tg_init(struct tg_core *g){memset(g,0,sizeof(*g));}
static inline void tg_clear(struct tg_core *g,unsigned int id)
{g->age[id]=g->need[id]=0;g->reported[id]=false;}
static inline void tg_apply(struct tg_core *g,const struct tm_core *c,struct tm_result *r,bool enabled)
{
 unsigned int i,j,n=0;
 if(!enabled)return;
 for(j=0;j<TA_N;j++){
  int label=c->labels[j];
  if(g->ttl[j])g->ttl[j]--;
  if(label>=0&&c->blocks[label].area>=TM_PALM_AREA)g->ttl[j]=TG_M+1U;
 }
 for(i=0;i<TM_MAX;i++){
  const struct tm_track *t=&c->tracks[i];
  if(!t->live){tg_clear(g,i);continue;}
  if(t->age==1)tg_clear(g,i);
  if(t->hold&&!g->reported[i]){g->age[i]=0;g->need[i]=TG_N;continue;}
  if(g->reported[i])continue;
  if(g->age[i]<TG_L)g->age[i]++;
  if(!g->need[i])g->need[i]=TG_N;
  for(j=0;j<TA_N;j++)if(g->ttl[j]){
   s64 dx=(s64)t->cx-(s64)(j&31)*256,dy=(s64)t->cy-(s64)(j>>5)*256;
   if(dx*dx+dy*dy<=(s64)TG_R*TG_R*65536){g->need[i]=TG_L;break;}
  }
 }
 for(i=0;i<(unsigned int)r->count;i++){
  unsigned int id=(unsigned int)r->points[i].id;
  if(!g->reported[id]&&g->age[id]>=g->need[id])g->reported[id]=true;
  if(g->reported[id])r->points[n++]=r->points[i];
 }
 r->count=(s32)n;
 /* Zero unused output slots, preserving the same deterministic host format. */
 for(i=n;i<TM_MAX;i++)memset(&r->points[i],0,sizeof(r->points[i]));
}
static inline void tg_words(const struct tg_core *g,const struct tm_core *c,const struct tm_result *r,s32 *words)
{
 unsigned int i,n=TM_WORDS;tm_words(c,r,words);
 for(i=0;i<TM_MAX;i++)words[n++]=(s32)g->age[i];
 for(i=0;i<TM_MAX;i++)words[n++]=(s32)g->need[i];
 for(i=0;i<TM_MAX;i++)words[n++]=g->reported[i];
 for(i=0;i<TA_N;i++)words[n++]=g->ttl[i];
}
#endif
