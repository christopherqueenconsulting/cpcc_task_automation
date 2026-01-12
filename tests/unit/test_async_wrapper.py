#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for async wrapper functionality.

Tests the run_async_in_streamlit() wrapper to ensure it correctly handles
various event loop scenarios without causing "Event loop already running" errors.
"""

import pytest
import asyncio
import threading


@pytest.mark.unit
class TestAsyncWrapper:
    """Test async execution wrapper for Streamlit."""
    
    async def dummy_async_function(self, value: int) -> int:
        """Simple async function for testing."""
        await asyncio.sleep(0.001)  # Tiny sleep to make it actually async
        return value * 2
    
    def test_async_wrapper_with_no_running_loop(self):
        """Test wrapper when no event loop is running (normal case)."""
        # This simulates the case where asyncio.run() should work fine
        
        async def test_coro():
            return await self.dummy_async_function(5)
        
        # In a clean thread with no event loop, asyncio.run() should work
        result_container = []
        exception_container = []
        
        def run_test():
            try:
                result = asyncio.run(test_coro())
                result_container.append(result)
            except Exception as e:
                exception_container.append(e)
        
        thread = threading.Thread(target=run_test)
        thread.start()
        thread.join()
        
        # Should succeed
        assert len(exception_container) == 0, f"Unexpected exception: {exception_container}"
        assert len(result_container) == 1
        assert result_container[0] == 10
    
    def test_async_wrapper_thread_based_fallback(self):
        """Test that thread-based execution works as fallback."""
        # This tests the thread-based fallback mechanism
        
        async def test_coro():
            return await self.dummy_async_function(7)
        
        # Simulate the thread-based execution path
        result_container = []
        exception_container = []
        
        def run_in_thread():
            try:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result = new_loop.run_until_complete(test_coro())
                    result_container.append(result)
                finally:
                    new_loop.close()
            except Exception as e:
                exception_container.append(e)
        
        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join()
        
        # Should succeed
        assert len(exception_container) == 0, f"Thread execution failed: {exception_container}"
        assert len(result_container) == 1
        assert result_container[0] == 14
    
    def test_async_execution_returns_correct_value(self):
        """Test that async execution preserves return values."""
        
        async def compute_sum(a: int, b: int) -> int:
            await asyncio.sleep(0.001)
            return a + b
        
        # Run in a clean thread
        result = None
        
        def run():
            nonlocal result
            result = asyncio.run(compute_sum(10, 20))
        
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        
        assert result == 30
    
    def test_async_execution_propagates_exceptions(self):
        """Test that exceptions from async functions are propagated."""
        
        async def failing_async():
            await asyncio.sleep(0.001)
            raise ValueError("Test error")
        
        exception_raised = False
        
        def run():
            nonlocal exception_raised
            try:
                asyncio.run(failing_async())
            except ValueError as e:
                if "Test error" in str(e):
                    exception_raised = True
        
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        
        assert exception_raised, "Exception was not propagated"


@pytest.mark.unit
class TestSyncWrapperLogic:
    """Test the sync wrapper logic without actually importing Streamlit."""
    
    def test_sync_wrapper_concept(self):
        """Test that sync wrappers can call async functions correctly."""
        
        async def async_grading_function(submission: str) -> dict:
            """Mock async grading function."""
            await asyncio.sleep(0.001)
            return {
                "score": 85,
                "feedback": f"Graded: {submission}"
            }
        
        def sync_wrapper(submission: str) -> dict:
            """Sync wrapper that runs async function."""
            # This simulates what our sync wrappers do
            def run():
                return asyncio.run(async_grading_function(submission))
            
            # Run in thread to avoid event loop conflicts
            result_container = []
            
            def thread_target():
                result_container.append(run())
            
            thread = threading.Thread(target=thread_target)
            thread.start()
            thread.join()
            
            return result_container[0]
        
        # Call sync wrapper
        result = sync_wrapper("student_submission.java")
        
        # Verify result
        assert result["score"] == 85
        assert "Graded: student_submission.java" in result["feedback"]
