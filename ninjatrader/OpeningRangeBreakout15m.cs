// -----------------------------------------------------------------------------
// OpeningRangeBreakout15m
//
// Estrategia NinjaTrader 8 — Rango de apertura NY 9:30-9:45 ET.
// Entrada: cierre de vela 5m con cuerpo fuera del rango.
// SL: extremo opuesto del rango. TP: 0.5R. Add-on al 0.4R = 2.5x size.
// Riesgo fijo $2000. Una operación por día. No cierra por tiempo.
//
// Spec completa: docs/strategy.md
// -----------------------------------------------------------------------------
#region Using declarations
using System;
using System.Collections.Generic;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class OpeningRangeBreakout15m : Strategy
    {
        private enum StratState
        {
            WaitRangeOpen,
            BuildingRange,
            Monitoring,
            Entered,
            ScaledIn,
            Done
        }

        private StratState state;
        private DateTime currentSessionDate;
        private double rangeHigh;
        private double rangeLow;
        private double rangeWidth;
        private bool isLong;
        private double entryPrice;
        private double stopPrice;
        private double tpPrice;
        private double addOnTriggerPrice;
        private int initialContracts;
        private int addOnContracts;
        private bool addOnExecuted;

        private const string EntryLongTag    = "ORB_LONG";
        private const string EntryShortTag   = "ORB_SHORT";
        private const string AddOnLongTag    = "ORB_LONG_ADD";
        private const string AddOnShortTag   = "ORB_SHORT_ADD";

        #region Parameters

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Riesgo USD", GroupName = "Strategy", Order = 0)]
        public double RiskUSD { get; set; }

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "TP R-multiple", GroupName = "Strategy", Order = 1)]
        public double TpRMultiple { get; set; }

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Add-on trigger R", GroupName = "Strategy", Order = 2)]
        public double AddOnTriggerR { get; set; }

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Add-on size multiple", GroupName = "Strategy", Order = 3)]
        public double AddOnSizeMultiple { get; set; }

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Range start (HH:mm NY)", GroupName = "Times", Order = 4)]
        public string RangeStart { get; set; }

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Range end (HH:mm NY)", GroupName = "Times", Order = 5)]
        public string RangeEnd { get; set; }

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Entry window end (HH:mm NY)", GroupName = "Times", Order = 6)]
        public string EntryWindowEnd { get; set; }

        [NinjaScriptProperty]
        [System.ComponentModel.Display(Name = "Max contracts (propfirm cap)", GroupName = "Strategy", Order = 7)]
        public int MaxContracts { get; set; }

        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description                 = "Opening Range Breakout NY 9:30-9:45 with 0.4R add-on";
                Name                        = "OpeningRangeBreakout15m";
                Calculate                   = Calculate.OnPriceChange;
                EntriesPerDirection         = 2;
                EntryHandling               = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = false;
                IsFillLimitOnTouch          = false;
                BarsRequiredToTrade         = 20;
                StartBehavior               = StartBehavior.WaitUntilFlat;
                TimeInForce                 = TimeInForce.Gtc;
                IncludeCommission           = true;

                RiskUSD             = 2000;
                TpRMultiple         = 0.5;
                AddOnTriggerR       = 0.4;
                AddOnSizeMultiple   = 2.5;
                RangeStart          = "09:30";
                RangeEnd            = "09:45";
                EntryWindowEnd      = "17:00";
                MaxContracts        = 10;
            }
            else if (State == State.Configure)
            {
                // Primary series must be 5-minute. We do NOT add additional series
                // — entries are detected on 5m bar close on this primary series.
            }
            else if (State == State.DataLoaded)
            {
                ResetState();
            }
        }

        private void ResetState()
        {
            state               = StratState.WaitRangeOpen;
            currentSessionDate  = DateTime.MinValue;
            rangeHigh           = 0;
            rangeLow            = 0;
            rangeWidth          = 0;
            isLong              = false;
            entryPrice          = 0;
            stopPrice           = 0;
            tpPrice             = 0;
            addOnTriggerPrice   = 0;
            initialContracts    = 0;
            addOnContracts      = 0;
            addOnExecuted       = false;
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BarsRequiredToTrade) return;
            if (BarsInProgress != 0) return;

            DateTime barNyTime = ToNYTime(Time[0]);
            DateTime sessionDate = barNyTime.Date;

            if (sessionDate != currentSessionDate)
            {
                if (Position.MarketPosition == MarketPosition.Flat)
                {
                    ResetState();
                    currentSessionDate = sessionDate;
                }
                else
                {
                    // Hubo overnight inesperado (no debería con propfirms). No tocamos hasta cerrar.
                    return;
                }
            }

            DateTime rangeStartDt    = sessionDate.Add(ParseTime(RangeStart));
            DateTime rangeEndDt      = sessionDate.Add(ParseTime(RangeEnd));
            DateTime entryWindowEnd  = sessionDate.Add(ParseTime(EntryWindowEnd));

            switch (state)
            {
                case StratState.WaitRangeOpen:
                    if (barNyTime >= rangeStartDt && barNyTime < rangeEndDt)
                    {
                        state = StratState.BuildingRange;
                        rangeHigh = High[0];
                        rangeLow  = Low[0];
                    }
                    break;

                case StratState.BuildingRange:
                    if (barNyTime < rangeEndDt)
                    {
                        if (High[0] > rangeHigh) rangeHigh = High[0];
                        if (Low[0]  < rangeLow ) rangeLow  = Low[0];
                    }
                    if (IsFirstTickOfBar && ToNYTime(Time[0]) >= rangeEndDt)
                    {
                        rangeWidth = rangeHigh - rangeLow;
                        state = StratState.Monitoring;
                        Print(string.Format("[{0:yyyy-MM-dd}] Range built: H={1} L={2} W={3} pts",
                            sessionDate, rangeHigh, rangeLow, rangeWidth));
                    }
                    break;

                case StratState.Monitoring:
                    if (barNyTime >= entryWindowEnd)
                    {
                        state = StratState.Done;
                        Print(string.Format("[{0:yyyy-MM-dd}] Sin ruptura válida hasta 17:00. Día cerrado.", sessionDate));
                        break;
                    }
                    if (IsFirstTickOfBar)
                    {
                        double prevClose = Close[1];
                        if (prevClose > rangeHigh)
                            TryEnter(true, prevClose);
                        else if (prevClose < rangeLow)
                            TryEnter(false, prevClose);
                    }
                    break;

                case StratState.Entered:
                    CheckAddOnTrigger();
                    break;

                case StratState.ScaledIn:
                    // Esperamos resolución por TP o SL — sin lógica intra-bar adicional.
                    break;

                case StratState.Done:
                    break;
            }
        }

        private void TryEnter(bool goLong, double breakoutClose)
        {
            isLong      = goLong;
            double refEntry = isLong ? rangeHigh : rangeLow;
            stopPrice  = goLong ? rangeLow : rangeHigh;
            // Entry market al cierre de la vela de ruptura (referencia del backtest);
            // el fill real lo determina el broker. Usamos breakoutClose para sizing.
            entryPrice = breakoutClose;
            tpPrice    = goLong ? refEntry + TpRMultiple * rangeWidth
                                : refEntry - TpRMultiple * rangeWidth;
            addOnTriggerPrice = goLong ? refEntry + AddOnTriggerR * rangeWidth
                                       : refEntry - AddOnTriggerR * rangeWidth;

            // Filtro overshoot: si el cierre ya pasó el TP, no entramos
            if ((goLong && entryPrice >= tpPrice) || (!goLong && entryPrice <= tpPrice))
            {
                Print(string.Format("[{0}] Overshoot del TP en la propia vela — entry={1} TP={2}. Skip.",
                    Time[0], entryPrice, tpPrice));
                state = StratState.Done;
                return;
            }

            double pointValue       = Instrument.MasterInstrument.PointValue;
            // Sizing basado en la distancia REAL entry → SL (no en rangeWidth) para
            // garantizar el riesgo máximo de $2000 incluso con overshoot del breakout.
            double actualStopPts    = Math.Abs(entryPrice - stopPrice);
            double riskPerContract  = actualStopPts * pointValue;

            if (riskPerContract <= 0)
            {
                Print("Risk per contract <= 0, no se opera.");
                state = StratState.Done;
                return;
            }

            initialContracts = (int)Math.Floor(RiskUSD / riskPerContract);
            if (MaxContracts > 0 && initialContracts > MaxContracts)
                initialContracts = MaxContracts;

            if (initialContracts <= 0)
            {
                Print(string.Format("Rango demasiado amplio para sizing (riskPerContract=${0}). No entra.", riskPerContract));
                state = StratState.Done;
                return;
            }

            addOnContracts = (int)Math.Floor(initialContracts * AddOnSizeMultiple);
            int totalAfterAddOn = initialContracts + addOnContracts;
            if (MaxContracts > 0 && totalAfterAddOn > MaxContracts)
            {
                addOnContracts = Math.Max(0, MaxContracts - initialContracts);
                Print(string.Format("Add-on recortado por cap de propfirm: {0} contratos (total {1}).",
                    addOnContracts, initialContracts + addOnContracts));
            }

            if (goLong)
            {
                EnterLong(initialContracts, EntryLongTag);
                SetStopLoss(EntryLongTag, CalculationMode.Price, stopPrice, false);
                SetProfitTarget(EntryLongTag, CalculationMode.Price, tpPrice);
            }
            else
            {
                EnterShort(initialContracts, EntryShortTag);
                SetStopLoss(EntryShortTag, CalculationMode.Price, stopPrice, false);
                SetProfitTarget(EntryShortTag, CalculationMode.Price, tpPrice);
            }

            state = StratState.Entered;
            addOnExecuted = false;

            Print(string.Format("[{0}] ENTRY {1} {2}@{3} SL={4} TP={5} addTrig={6} risk/contract=${7}",
                Time[0], goLong ? "LONG" : "SHORT", initialContracts, entryPrice,
                stopPrice, tpPrice, addOnTriggerPrice, riskPerContract));
        }

        private void CheckAddOnTrigger()
        {
            if (addOnExecuted) return;
            if (addOnContracts <= 0)
            {
                state = StratState.ScaledIn;
                return;
            }

            double last = Close[0];
            bool triggered = isLong ? last >= addOnTriggerPrice : last <= addOnTriggerPrice;
            if (!triggered) return;

            // 1. Mete el add-on a mercado
            string addTag = isLong ? AddOnLongTag : AddOnShortTag;
            if (isLong) EnterLong(addOnContracts, addTag);
            else        EnterShort(addOnContracts, addTag);

            // 2. Mueve el SL de TODA la posición al precio de entrada original
            double refEntry = isLong ? rangeHigh : rangeLow;
            double newStopPrice = refEntry;
            SetStopLoss(isLong ? EntryLongTag : EntryShortTag, CalculationMode.Price, newStopPrice, false);
            SetStopLoss(addTag, CalculationMode.Price, newStopPrice, false);
            SetProfitTarget(addTag, CalculationMode.Price, tpPrice);

            addOnExecuted = true;
            state = StratState.ScaledIn;

            Print(string.Format("[{0}] ADD-ON {1} {2}@{3} (trigger {4}). SL movido a {5} (entry original).",
                Time[0], isLong ? "LONG" : "SHORT", addOnContracts, last, addOnTriggerPrice, newStopPrice));
        }

        protected override void OnPositionUpdate(Position position, double averagePrice, int quantity, MarketPosition marketPosition)
        {
            if (marketPosition == MarketPosition.Flat && (state == StratState.Entered || state == StratState.ScaledIn))
            {
                state = StratState.Done;
                Print(string.Format("[{0}] Posición cerrada (TP o SL). Día terminado.", Time[0]));
            }
        }

        private DateTime ToNYTime(DateTime t)
        {
            try
            {
                TimeZoneInfo et = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
                return TimeZoneInfo.ConvertTime(t, TimeZoneInfo.Local, et);
            }
            catch
            {
                return t;
            }
        }

        private TimeSpan ParseTime(string hhmm)
        {
            string[] parts = hhmm.Split(':');
            return new TimeSpan(int.Parse(parts[0]), int.Parse(parts[1]), 0);
        }
    }
}
