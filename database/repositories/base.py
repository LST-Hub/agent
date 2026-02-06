"""
Abstract Base Repository.
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, List, Optional
from sqlalchemy.orm import Session

T = TypeVar('T')

class BaseRepository(Generic[T], ABC):
    """
    Abstract base class for all repositories.
    Enforces standard CRUD interface.
    """
    
    def __init__(self, session: Session):
        self.session = session

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[T]:
        pass

    @abstractmethod
    def get_all(self) -> List[T]:
        pass
