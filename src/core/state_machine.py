"""
Trade Lifecycle State Machine
Deterministic FSM for execution control
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Dict, Optional, Set
from uuid import UUID

from src.core.contracts import (
    OpportunityCandidate,
    OrderSide,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger

logger = get_logger("state_machine")


class StateTransitionError(Exception):
    """Invalid state transition attempted"""
    pass


class StateMachine:
    """
    Trade Lifecycle State Machine
    
    Valid transitions:
    IDLE -> VALIDATING
    VALIDATING -> PRE_FLIGHT | ERROR_RECOVERY
    PRE_FLIGHT -> PENDING_OPEN | ERROR_RECOVERY
    PENDING_OPEN -> OPEN_PARTIAL | ACTIVE_HEDGED | ERROR_RECOVERY
    OPEN_PARTIAL -> ACTIVE_HEDGED | ERROR_RECOVERY | PENDING_CLOSE (panic)
    ACTIVE_HEDGED -> PENDING_CLOSE
    PENDING_CLOSE -> RECONCILIATION | ERROR_RECOVERY
    RECONCILIATION -> CLOSED
    ERROR_RECOVERY -> CLOSED | PENDING_CLOSE (if partial)
    """
    
    # Define valid transitions
    VALID_TRANSITIONS: Dict[TradeState, Set[TradeState]] = {
        TradeState.IDLE: {
            TradeState.VALIDATING
        },
        TradeState.VALIDATING: {
            TradeState.PRE_FLIGHT,
            TradeState.ERROR_RECOVERY
        },
        TradeState.PRE_FLIGHT: {
            TradeState.PENDING_OPEN,
            TradeState.ERROR_RECOVERY
        },
        TradeState.PENDING_OPEN: {
            TradeState.OPEN_PARTIAL,
            TradeState.ACTIVE_HEDGED,
            TradeState.ERROR_RECOVERY
        },
        TradeState.OPEN_PARTIAL: {
            TradeState.ACTIVE_HEDGED,
            TradeState.PENDING_CLOSE,  # Panic close
            TradeState.ERROR_RECOVERY
        },
        TradeState.ACTIVE_HEDGED: {
            TradeState.PENDING_CLOSE
        },
        TradeState.PENDING_CLOSE: {
            TradeState.RECONCILIATION,
            TradeState.ERROR_RECOVERY
        },
        TradeState.RECONCILIATION: {
            TradeState.CLOSED
        },
        TradeState.ERROR_RECOVERY: {
            TradeState.CLOSED,
            TradeState.PENDING_CLOSE  # If partial fills exist
        },
    }
    
    def __init__(self, trade: TradeRecord):
        self.trade = trade
        self._state_entered_at = datetime.utcnow()
        self._state_handlers: Dict[TradeState, Callable] = {}
        self._timeout_tasks: Dict[TradeState, asyncio.Task] = {}
    
    def can_transition_to(self, new_state: TradeState) -> bool:
        """Check if transition is valid"""
        current = self.trade.state
        return new_state in self.VALID_TRANSITIONS.get(current, set())
    
    def transition_to(self, new_state: TradeState, reason: Optional[str] = None):
        """
        Execute state transition
        Raises StateTransitionError if invalid
        """
        current = self.trade.state
        
        # Validate transition
        if not self.can_transition_to(new_state):
            error_msg = f"Invalid transition: {current} -> {new_state}"
            logger.error(
                error_msg,
                trade_id=self.trade.trade_id,
                current_state=current.value,
                attempted_state=new_state.value
            )
            raise StateTransitionError(error_msg)
        
        # Calculate time in current state
        time_in_state = datetime.utcnow() - self._state_entered_at
        
        # Log transition
        logger.audit_trade_state(
            trade_id=self.trade.trade_id,
            old_state=current.value,
            new_state=new_state.value,
            reason=reason
        )
        
        logger.info(
            f"State transition: {current.value} -> {new_state.value}",
            trade_id=self.trade.trade_id,
            symbol=self.trade.opportunity.symbol if self.trade.opportunity else None,
            time_in_state_ms=time_in_state.total_seconds() * 1000,
            reason=reason
        )
        
        # Update trade record
        self.trade.transition_state(new_state)
        self._state_entered_at = datetime.utcnow()
        
        # Cancel any running timeout for previous state
        self._cancel_timeout(current)
    
    def force_transition(self, new_state: TradeState, reason: str):
        """
        Force transition without validation
        Use only in emergency/recovery scenarios
        """
        current = self.trade.state
        
        logger.warning(
            f"FORCED state transition: {current.value} -> {new_state.value}",
            trade_id=self.trade.trade_id,
            reason=reason
        )
        
        self.trade.transition_state(new_state)
        self._state_entered_at = datetime.utcnow()
    
    def get_state_duration(self) -> timedelta:
        """Get time spent in current state"""
        return datetime.utcnow() - self._state_entered_at
    
    def register_handler(self, state: TradeState, handler: Callable):
        """Register async handler for state entry"""
        self._state_handlers[state] = handler
    
    async def execute_state_handler(self, state: TradeState):
        """Execute handler for current state if registered"""
        handler = self._state_handlers.get(state)
        if handler:
            try:
                await handler(self.trade)
            except Exception as e:
                logger.error(
                    f"State handler failed: {state.value}",
                    trade_id=self.trade.trade_id,
                    exc_info=True,
                    error=str(e)
                )
                raise
    
    def set_timeout(
        self,
        state: TradeState,
        timeout_ms: int,
        callback: Callable
    ) -> asyncio.Task:
        """
        Set timeout for current state
        Callback is invoked if state not exited in time
        """
        async def timeout_handler():
            await asyncio.sleep(timeout_ms / 1000)
            
            # Check if still in same state
            if self.trade.state == state:
                logger.warning(
                    f"State timeout: {state.value} exceeded {timeout_ms}ms",
                    trade_id=self.trade.trade_id
                )
                await callback(self.trade)
        
        task = asyncio.create_task(timeout_handler())
        self._timeout_tasks[state] = task
        return task
    
    def _cancel_timeout(self, state: TradeState):
        """Cancel timeout task for state"""
        task = self._timeout_tasks.get(state)
        if task and not task.done():
            task.cancel()
            self._timeout_tasks.pop(state, None)
    
    def cleanup(self):
        """Cancel all pending timeouts"""
        for task in self._timeout_tasks.values():
            if not task.done():
                task.cancel()
        self._timeout_tasks.clear()
    
    # ==================== STATE VALIDATORS ====================
    
    def validate_can_open(self) -> tuple[bool, Optional[str]]:
        """Validate if trade can be opened"""
        if not self.trade.opportunity:
            return False, "No opportunity attached"
        
        if self.trade.opportunity.is_expired():
            return False, "Opportunity expired"
        
        if not self.trade.opportunity.is_profitable():
            return False, "Not profitable after worst-case"
        
        return True, None
    
    def validate_hedge_complete(self) -> bool:
        """Check if both legs are filled"""
        return self.trade.is_hedged
    
    def validate_can_close(self) -> tuple[bool, Optional[str]]:
        """Validate if trade can be closed"""
        if not self.trade.is_hedged:
            # Allow close if we have orphaned positions
            if self.trade.long_leg and self.trade.long_leg.filled_quantity > 0:
                return True, "Closing orphaned long position"
            if self.trade.short_leg and self.trade.short_leg.filled_quantity > 0:
                return True, "Closing orphaned short position"
            return False, "No positions to close"
        
        return True, None
    
    # ==================== CONVENIENCE METHODS ====================
    
    @property
    def is_terminal(self) -> bool:
        """Check if in terminal state"""
        return self.trade.state == TradeState.CLOSED
    
    @property
    def is_opening(self) -> bool:
        """Check if in opening phase"""
        return self.trade.state in [
            TradeState.VALIDATING,
            TradeState.PRE_FLIGHT,
            TradeState.PENDING_OPEN,
            TradeState.OPEN_PARTIAL
        ]
    
    @property
    def is_active(self) -> bool:
        """Check if trade is active"""
        return self.trade.state == TradeState.ACTIVE_HEDGED
    
    @property
    def is_closing(self) -> bool:
        """Check if in closing phase"""
        return self.trade.state in [
            TradeState.PENDING_CLOSE,
            TradeState.RECONCILIATION
        ]
    
    @property
    def is_error(self) -> bool:
        """Check if in error state"""
        return self.trade.state == TradeState.ERROR_RECOVERY
    
    def __repr__(self) -> str:
        duration = self.get_state_duration()
        return (
            f"StateMachine(trade_id={self.trade.trade_id}, "
            f"state={self.trade.state.value}, "
            f"duration={duration.total_seconds():.2f}s)"
        )


class TradeLifecycleManager:
    """
    Manages multiple trade state machines
    """
    
    def __init__(self):
        self._state_machines: Dict[UUID, StateMachine] = {}
    
    def create_trade(self, opportunity: OpportunityCandidate) -> StateMachine:
        """Create new trade and state machine"""
        trade = TradeRecord(opportunity=opportunity)
        sm = StateMachine(trade)
        
        self._state_machines[trade.trade_id] = sm
        
        logger.info(
            "Trade created",
            trade_id=trade.trade_id,
            opportunity_id=opportunity.opportunity_id,
            symbol=opportunity.symbol
        )
        
        return sm
    
    def get_state_machine(self, trade_id: UUID) -> Optional[StateMachine]:
        """Get state machine for trade"""
        return self._state_machines.get(trade_id)
    
    def remove_trade(self, trade_id: UUID):
        """Remove completed trade"""
        sm = self._state_machines.get(trade_id)
        if sm:
            sm.cleanup()
            del self._state_machines[trade_id]
    
    def get_active_trades(self) -> list[StateMachine]:
        """Get all active (non-terminal) trades"""
        return [
            sm for sm in self._state_machines.values()
            if not sm.is_terminal
        ]
    
    def get_trades_in_state(self, state: TradeState) -> list[StateMachine]:
        """Get all trades in specific state"""
        return [
            sm for sm in self._state_machines.values()
            if sm.trade.state == state
        ]
    
    def count_active(self) -> int:
        """Count active trades"""
        return len(self.get_active_trades())
    
    def cleanup_completed(self, max_age_hours: int = 24):
        """Remove old completed trades"""
        now = datetime.utcnow()
        to_remove = []
        
        for trade_id, sm in self._state_machines.items():
            if sm.is_terminal and sm.trade.timestamp_closed:
                age = now - sm.trade.timestamp_closed
                if age.total_seconds() > max_age_hours * 3600:
                    to_remove.append(trade_id)
        
        for trade_id in to_remove:
            self.remove_trade(trade_id)
        
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} completed trades")
